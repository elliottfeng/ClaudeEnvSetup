"""远端版本解析：官方 → 镜像 → 内置固定版本。"""

from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import constants, http_utils
from .config import AppConfig
from .models import Arch, ComponentId, ReleaseInfo, UrlCandidate
from .versioning import is_prerelease, parse_version

_GIT_TAG_RE = re.compile(r"^v(\d+\.\d+\.\d+)\.windows\.(\d+)$")
_PYTHON_NAME_RE = re.compile(r"^Python\s+(\d+\.\d+\.\d+)$")
_PYTHON_DIR_RE = re.compile(r"^(\d+\.\d+\.\d+)/$")
_GIT_DIR_RE = re.compile(r"^v(\d+\.\d+\.\d+)\.windows\.(\d+)/$")


def arch_name(arch: Arch) -> str:
    return "arm64" if arch == Arch.ARM64 else "x64"


def _python_arch(arch: Arch) -> str:
    return "arm64" if arch == Arch.ARM64 else "amd64"


def _git_suffix(arch: Arch) -> str:
    return "arm64" if arch == Arch.ARM64 else "64-bit"


def _sources(mode: str) -> list[str]:
    if mode == "official":
        return ["official"]
    if mode == "mirror":
        return ["mirror"]
    return ["official", "mirror"]


def _release(
    cid: ComponentId,
    version: str,
    filename: str,
    urls: list[UrlCandidate],
    is_pinned: bool = False,
    note: str = "",
) -> ReleaseInfo:
    return ReleaseInfo(
        component=cid,
        version=version,
        version_tuple=parse_version(version) or (),
        filename=filename,
        urls=tuple(urls),
        is_pinned=is_pinned,
        note=note,
    )


def _node_filename(version: str, arch: Arch) -> str:
    return f"node-v{version}-{arch_name(arch)}.msi"


def _node_release(version: str, arch: Arch, is_pinned: bool = False, note: str = "") -> ReleaseInfo:
    filename = _node_filename(version, arch)
    urls = [
        UrlCandidate(f"{constants.NODE_DIST_BASE}/v{version}/{filename}", "official"),
        UrlCandidate(f"{constants.NODE_DIST_MIRROR}/v{version}/{filename}", "mirror"),
    ]
    return _release(ComponentId.NODE, version, filename, urls, is_pinned, note)


def parse_node_index(data: Any, arch: Arch) -> ReleaseInfo | None:
    if not isinstance(data, list):
        return None
    best: tuple[tuple[int, ...], str, str] | None = None
    for entry in data:
        if not isinstance(entry, dict) or not entry.get("lts"):
            continue
        version_text = str(entry.get("version", "")).lstrip("vV")
        version = parse_version(version_text)
        if not version or is_prerelease(version_text):
            continue
        files = entry.get("files") or []
        if f"win-{arch_name(arch)}-msi" not in files:
            continue
        if best is None or version > best[0]:
            best = (version, version_text, str(entry.get("lts", "")))
    if not best:
        return None
    _, version_text, lts_name = best
    return _node_release(version_text, arch, note=f"LTS {lts_name}".strip())


def _git_base_version(tag: str) -> str | None:
    match = _GIT_TAG_RE.match(tag.strip())
    return match.group(1) if match else None


def _git_release(tag: str, arch: Arch, is_pinned: bool = False) -> ReleaseInfo | None:
    base = _git_base_version(tag)
    if not base:
        return None
    filename = f"Git-{base}-{_git_suffix(arch)}.exe"
    urls = [
        UrlCandidate(
            f"https://github.com/git-for-windows/git/releases/download/{tag}/{filename}",
            "official",
        ),
        UrlCandidate(f"{constants.GIT_MIRROR_BASE}/{tag}/{filename}", "mirror"),
    ]
    return _release(ComponentId.GIT, base, filename, urls, is_pinned, note=tag)


def parse_git_release(data: Any, arch: Arch) -> ReleaseInfo | None:
    if not isinstance(data, dict) or data.get("prerelease"):
        return None
    tag = str(data.get("tag_name", ""))
    base = _git_base_version(tag)
    if not base:
        return None
    filename = f"Git-{base}-{_git_suffix(arch)}.exe"
    asset_url = None
    for asset in data.get("assets") or []:
        if isinstance(asset, dict) and asset.get("name") == filename:
            asset_url = asset.get("browser_download_url")
            break
    if not asset_url:
        return None
    return _release(
        ComponentId.GIT,
        base,
        filename,
        [UrlCandidate(str(asset_url), "official"), UrlCandidate(f"{constants.GIT_MIRROR_BASE}/{tag}/{filename}", "mirror")],
        note=tag,
    )


def parse_git_mirror_listing(data: Any, arch: Arch) -> ReleaseInfo | None:
    if not isinstance(data, list):
        return None
    candidates: list[tuple[tuple[int, ...], int, str]] = []
    for entry in data:
        if not isinstance(entry, dict) or entry.get("type") != "dir":
            continue
        name = str(entry.get("name", ""))
        match = _GIT_DIR_RE.match(name)
        if not match:
            continue
        version = parse_version(match.group(1))
        if version:
            candidates.append((version, int(match.group(2)), f"v{match.group(1)}.windows.{match.group(2)}"))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return _git_release(candidates[0][2], arch)


def _python_matches(version: str, wanted: str) -> bool:
    wanted = (wanted or "").strip()
    if not wanted:
        return True
    if wanted.count(".") >= 2:
        return version == wanted
    return version.rsplit(".", 1)[0] == wanted


def _python_release(version: str, arch: Arch, is_pinned: bool = False) -> ReleaseInfo:
    filename = f"python-{version}-{_python_arch(arch)}.exe"
    urls = [
        UrlCandidate(f"{constants.PYTHON_FTP_BASE}/{version}/{filename}", "official"),
        UrlCandidate(f"{constants.PYTHON_MIRROR_BASE}/{version}/{filename}", "mirror"),
    ]
    return _release(ComponentId.PYTHON, version, filename, urls, is_pinned)


def parse_python_releases(data: Any, arch: Arch, wanted: str = "3.15") -> ReleaseInfo | None:
    if not isinstance(data, list):
        return None
    best: tuple[tuple[int, ...], str] | None = None
    for entry in data:
        if not isinstance(entry, dict) or entry.get("pre_release"):
            continue
        match = _PYTHON_NAME_RE.match(str(entry.get("name", "")))
        if not match:
            continue
        version_text = match.group(1)
        if not _python_matches(version_text, wanted):
            continue
        if is_prerelease(str(entry.get("name", ""))):
            continue
        version = parse_version(version_text)
        if version and (best is None or version > best[0]):
            best = (version, version_text)
    if not best:
        return None
    return _python_release(best[1], arch)


def parse_python_mirror_listing(data: Any, arch: Arch, wanted: str = "3.15") -> ReleaseInfo | None:
    if not isinstance(data, list):
        return None
    candidates: list[tuple[tuple[int, ...], str]] = []
    for entry in data:
        if not isinstance(entry, dict) or entry.get("type") != "dir":
            continue
        match = _PYTHON_DIR_RE.match(str(entry.get("name", "")))
        if not match:
            continue
        version_text = match.group(1)
        if not _python_matches(version_text, wanted):
            continue
        version = parse_version(version_text)
        if version:
            candidates.append((version, version_text))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return _python_release(candidates[0][1], arch)


def parse_vscode_update(data: Any, arch: Arch) -> ReleaseInfo | None:
    if not isinstance(data, dict):
        return None
    version = str(data.get("productVersion") or data.get("name") or "")
    url = str(data.get("url") or "")
    if not version or not url:
        return None
    filename = url.rsplit("/", 1)[-1]
    candidate = UrlCandidate(url, "official", data.get("sha256hash") or None)
    return _release(ComponentId.VSCODE, version, filename, [candidate])


def parse_claude_npm(data: Any) -> ReleaseInfo | None:
    if not isinstance(data, dict):
        return None
    version = str(data.get("version", ""))
    if not version:
        return None
    return _release(ComponentId.CLAUDE, version, "", [])


def pinned_release(cid: ComponentId, arch: Arch, python_version: str = "3.15") -> ReleaseInfo | None:
    if cid == ComponentId.NODE:
        return _node_release(constants.PINNED_NODE, arch, True, "内置版本")
    if cid == ComponentId.GIT:
        release = _git_release(f"v{constants.PINNED_GIT}", arch, True)
        if release:
            return ReleaseInfo(
                component=release.component,
                version=release.version,
                version_tuple=release.version_tuple,
                filename=release.filename,
                urls=release.urls,
                is_pinned=True,
                note="内置版本",
            )
        return None
    if cid == ComponentId.PYTHON:
        wanted = (python_version or "").strip()
        if wanted and wanted != "3.15":
            if wanted.count(".") >= 2:
                return _python_release(wanted, arch, True)
            return None
        return _python_release(constants.PINNED_PYTHON, arch, True)
    if cid == ComponentId.VSCODE:
        filename = f"VSCodeUserSetup-{arch_name(arch)}-{constants.PINNED_VSCODE}.exe"
        url = constants.VSCODE_PINNED_URL.format(version=constants.PINNED_VSCODE, arch=arch_name(arch))
        return _release(ComponentId.VSCODE, constants.PINNED_VSCODE, filename, [UrlCandidate(url, "official")], True)
    if cid == ComponentId.CLAUDE:
        return _release(ComponentId.CLAUDE, constants.PINNED_CLAUDE, "", [], True)
    return None


def _node_checksum(version: str, filename: str, mode: str, proxy: str | None) -> str | None:
    locations = []
    if "official" in _sources(mode):
        locations.append(f"{constants.NODE_DIST_BASE}/v{version}/SHASUMS256.txt")
    if "mirror" in _sources(mode):
        locations.append(f"{constants.NODE_DIST_MIRROR}/v{version}/SHASUMS256.txt")
    for url in locations:
        try:
            text = http_utils.get_text(url, proxy=proxy)
        except (http_utils.HttpError, http_utils.NetworkError):
            continue
        for line in text.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == filename:
                return parts[0].strip().lower()
    return None


class ReleaseCache:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else constants.cache_dir() / "releases.json"
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}

    def get(
        self,
        cid: ComponentId,
        arch: Arch,
        ttl_hours: int,
        variant: str = "",
    ) -> ReleaseInfo | None:
        with self._lock:
            data = self._load()
        key = f"{cid.value}:{arch.value}" + (f":{variant}" if variant else "")
        item = (data.get("releases") or {}).get(key)
        if not isinstance(item, dict):
            return None
        try:
            saved = datetime.fromisoformat(str(data.get("timestamp", "")))
            age_hours = (datetime.now(timezone.utc) - saved).total_seconds() / 3600
        except (ValueError, TypeError):
            return None
        if age_hours > ttl_hours:
            return None
        try:
            urls = tuple(
                UrlCandidate(str(u["url"]), str(u.get("source", "official")), u.get("sha256"))
                for u in item.get("urls", [])
                if isinstance(u, dict) and u.get("url")
            )
            return ReleaseInfo(
                component=cid,
                version=str(item["version"]),
                version_tuple=parse_version(str(item["version"])) or (),
                filename=str(item.get("filename", "")),
                urls=urls,
                is_pinned=False,
                note=str(item.get("note", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def set(
        self,
        cid: ComponentId,
        arch: Arch,
        release: ReleaseInfo,
        variant: str = "",
    ) -> None:
        with self._lock:
            data = self._load()
            releases = data.setdefault("releases", {})
            key = f"{cid.value}:{arch.value}" + (f":{variant}" if variant else "")
            releases[key] = {
                "version": release.version,
                "filename": release.filename,
                "note": release.note,
                "urls": [
                    {"url": candidate.url, "source": candidate.source, "sha256": candidate.sha256}
                    for candidate in release.urls
                ],
            }
            data["timestamp"] = datetime.now(timezone.utc).isoformat()
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temp = self.path.with_name(self.path.name + ".tmp")
                temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                temp.replace(self.path)
            except OSError:
                pass


def _resolve_node(arch: Arch, config: AppConfig, use_network: bool) -> ReleaseInfo | None:
    mode = config.network.mode
    proxy = config.network.proxy
    for source in _sources(mode):
        url = constants.NODE_INDEX_URL if source == "official" else f"{constants.NODE_DIST_MIRROR}/index.json"
        if not use_network:
            break
        try:
            data = http_utils.get_json(url, proxy=proxy)
        except (http_utils.HttpError, http_utils.NetworkError):
            continue
        release = parse_node_index(data, arch)
        if release:
            checksum = _node_checksum(release.version, release.filename, mode, proxy)
            if checksum:
                release = ReleaseInfo(
                    component=release.component,
                    version=release.version,
                    version_tuple=release.version_tuple,
                    filename=release.filename,
                    urls=tuple(
                        UrlCandidate(item.url, item.source, checksum) for item in release.urls
                    ),
                    is_pinned=False,
                    note=release.note,
                )
            return release
    return pinned_release(ComponentId.NODE, arch)


def _resolve_git(arch: Arch, config: AppConfig, use_network: bool) -> ReleaseInfo | None:
    mode = config.network.mode
    proxy = config.network.proxy
    if use_network and "official" in _sources(mode):
        try:
            data = http_utils.get_json(
                constants.GIT_LATEST_API,
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                proxy=proxy,
            )
            release = parse_git_release(data, arch)
            if release:
                return release
        except (http_utils.HttpError, http_utils.NetworkError):
            pass
    if use_network and "mirror" in _sources(mode):
        try:
            data = http_utils.get_json(f"{constants.GIT_MIRROR_BASE}/", proxy=proxy)
            release = parse_git_mirror_listing(data, arch)
            if release:
                return release
        except (http_utils.HttpError, http_utils.NetworkError):
            pass
    return pinned_release(ComponentId.GIT, arch)


def _resolve_python(arch: Arch, config: AppConfig, use_network: bool) -> ReleaseInfo | None:
    mode = config.network.mode
    proxy = config.network.proxy
    wanted = config.python_version or "3.15"
    if use_network and "official" in _sources(mode):
        try:
            data = http_utils.get_json(constants.PYTHON_RELEASE_API, proxy=proxy)
            release = parse_python_releases(data, arch, wanted)
            if release:
                return release
        except (http_utils.HttpError, http_utils.NetworkError):
            pass
    if use_network and "mirror" in _sources(mode):
        try:
            data = http_utils.get_json(f"{constants.PYTHON_MIRROR_BASE}/", proxy=proxy)
            release = parse_python_mirror_listing(data, arch, wanted)
            if release:
                return release
        except (http_utils.HttpError, http_utils.NetworkError):
            pass
    return pinned_release(ComponentId.PYTHON, arch, wanted)


def _resolve_vscode(arch: Arch, config: AppConfig, use_network: bool) -> ReleaseInfo | None:
    proxy = config.network.proxy
    if use_network and config.network.mode != "mirror":
        try:
            url = constants.VSCODE_UPDATE_API.format(arch=arch_name(arch))
            data = http_utils.get_json(url, proxy=proxy)
            release = parse_vscode_update(data, arch)
            if release:
                return release
        except (http_utils.HttpError, http_utils.NetworkError):
            pass
    return pinned_release(ComponentId.VSCODE, arch)


def _resolve_claude(arch: Arch, config: AppConfig, use_network: bool) -> ReleaseInfo | None:
    proxy = config.network.proxy
    mode = config.network.mode
    if use_network:
        for source in _sources(mode):
            url = constants.NPM_CLAUDE_LATEST if source == "official" else constants.NPM_MIRROR_CLAUDE_LATEST
            try:
                data = http_utils.get_json(url, proxy=proxy)
                release = parse_claude_npm(data)
                if release:
                    return release
            except (http_utils.HttpError, http_utils.NetworkError):
                continue
    return pinned_release(ComponentId.CLAUDE, arch)


_RESOLVERS = {
    ComponentId.NODE: _resolve_node,
    ComponentId.GIT: _resolve_git,
    ComponentId.PYTHON: _resolve_python,
    ComponentId.VSCODE: _resolve_vscode,
    ComponentId.CLAUDE: _resolve_claude,
}


def resolve_component(
    cid: ComponentId,
    arch: Arch,
    config: AppConfig,
    use_network: bool = True,
    cache: ReleaseCache | None = None,
) -> ReleaseInfo | None:
    variant = config.python_version if cid == ComponentId.PYTHON else ""
    if use_network and cache is not None:
        cached = cache.get(cid, arch, config.network.cache_ttl_hours, variant)
        if cached is not None:
            return cached
    try:
        release = _RESOLVERS[cid](arch, config, use_network)
    except Exception:
        release = pinned_release(cid, arch, config.python_version)
    if release and not release.is_pinned and use_network and cache is not None:
        cache.set(cid, arch, release, variant)
    return release


def resolve_all(
    arch: Arch,
    config: AppConfig,
    use_network: bool = True,
    cache: ReleaseCache | None = None,
) -> dict[ComponentId, ReleaseInfo | None]:
    cache = cache or ReleaseCache()
    results: dict[ComponentId, ReleaseInfo | None] = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            cid: pool.submit(resolve_component, cid, arch, config, use_network, cache)
            for cid in _RESOLVERS
        }
        for cid, future in futures.items():
            try:
                results[cid] = future.result()
            except Exception:
                results[cid] = pinned_release(cid, arch, config.python_version)
    order = [ComponentId.CLAUDE, ComponentId.GIT, ComponentId.NODE, ComponentId.PYTHON, ComponentId.VSCODE]
    return {cid: results.get(cid) for cid in order}
