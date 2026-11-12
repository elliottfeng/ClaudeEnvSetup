"""断点续传下载器：进度、取消、重试、SHA256 与原子改名。"""

from __future__ import annotations

import hashlib
import http.client
import os
import re
import shutil
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from . import constants
from .models import ComponentId, ReleaseInfo, UrlCandidate

ProgressCb = Callable[[ComponentId, int | None, int, float], None]

_ILLEGAL_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class DownloadCancelled(Exception):
    pass


class DownloadError(Exception):
    pass


def safe_filename(name: str) -> str:
    cleaned = _ILLEGAL_FILENAME.sub("_", name).strip().rstrip(". ")
    return cleaned or "installer.bin"


def file_sha256(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _opener(proxy: str | None) -> urllib.request.OpenerDirector:
    if proxy:
        handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})]
    else:
        handlers = [urllib.request.ProxyHandler()]
    handlers.append(urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    return urllib.request.build_opener(*handlers)


class DownloadManager:
    def __init__(
        self,
        proxy: str | None = None,
        retries: int = constants.DOWNLOAD_RETRIES,
        read_timeout: int = constants.DOWNLOAD_READ_TIMEOUT,
    ) -> None:
        self.proxy = proxy
        self.retries = max(1, retries)
        self.read_timeout = read_timeout

    def fetch(
        self,
        release: ReleaseInfo,
        dest_dir: str | Path,
        progress: ProgressCb | None = None,
        cancel: threading.Event | None = None,
    ) -> Path:
        if not release.urls:
            raise DownloadError("该组件不支持预下载安装包")
        destination = Path(dest_dir)
        try:
            destination.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DownloadError(f"无法创建下载目录: {destination} ({exc})") from exc
        filename = safe_filename(release.filename or (release.urls[0].url.rsplit("/", 1)[-1]))
        target = destination / filename
        part = destination / (filename + ".part")

        if target.exists():
            if release.urls and release.urls[0].sha256:
                try:
                    if file_sha256(target) == release.urls[0].sha256.lower():
                        return target
                except OSError:
                    pass
            else:
                try:
                    if target.stat().st_size > 0:
                        return target
                except OSError:
                    pass

        expected_size = release.size
        try:
            free = shutil.disk_usage(destination).free
        except OSError:
            free = None
        if free is not None:
            if expected_size and free < expected_size + 50 * 1024 * 1024:
                raise DownloadError("下载目录剩余空间不足")
            if not expected_size and free < 100 * 1024 * 1024:
                raise DownloadError("下载目录剩余空间不足（至少需要 100MB）")

        candidates = self._probe_order(release.urls, cancel)
        errors: list[str] = []
        for candidate in candidates:
            for attempt in range(self.retries):
                if cancel and cancel.is_set():
                    raise DownloadCancelled()
                try:
                    self._download_one(release.component, candidate, part, progress, cancel)
                    if candidate.sha256:
                        actual = file_sha256(part)
                        if actual.lower() != candidate.sha256.lower():
                            try:
                                part.unlink(missing_ok=True)
                            except OSError:
                                pass
                            raise DownloadError("SHA256 校验失败")
                    os.replace(part, target)
                    return target
                except DownloadCancelled:
                    raise
                except DownloadError as exc:
                    errors.append(f"{candidate.source}: {exc}")
                    if "SHA256" in str(exc):
                        # 校验失败删除临时文件后重试一次
                        continue
                    if attempt + 1 < self.retries:
                        self._wait(attempt, cancel)
                        continue
                except urllib.error.HTTPError as exc:
                    errors.append(f"{candidate.source}: HTTP {exc.code}")
                    if exc.code == 416:
                        try:
                            part.unlink(missing_ok=True)
                        except OSError:
                            pass
                        if attempt + 1 < self.retries:
                            self._wait(attempt, cancel)
                            continue
                    if exc.code in (408, 429, 500, 502, 503, 504) and attempt + 1 < self.retries:
                        self._wait(attempt, cancel)
                        continue
                    break
                except (
                    urllib.error.URLError,
                    ssl.SSLError,
                    http.client.HTTPException,
                    TimeoutError,
                    OSError,
                ) as exc:
                    errors.append(f"{candidate.source}: {exc}")
                    if attempt + 1 < self.retries:
                        self._wait(attempt, cancel)
                        continue
        detail = "；".join(errors[-4:]) if errors else "未知错误"
        raise DownloadError(f"下载失败（已尝试官方/镜像源）: {detail}")

    def _wait(self, attempt: int, cancel: threading.Event | None) -> None:
        delay = min(4.0, 1.0 * (2**attempt))
        if cancel is not None:
            cancel.wait(delay)
        else:
            time.sleep(delay)

    def _probe_order(
        self,
        candidates: tuple[UrlCandidate, ...],
        cancel: threading.Event | None,
    ) -> list[UrlCandidate]:
        """用 1 字节 Range 请求快速探测源可达性，避免在不可达源上长时间等待。"""
        if len(candidates) <= 1:
            return list(candidates)
        reachable: list[UrlCandidate] = []
        unreachable: list[UrlCandidate] = []
        for candidate in candidates:
            if cancel is not None and cancel.is_set():
                raise DownloadCancelled()
            try:
                request = urllib.request.Request(
                    candidate.url,
                    headers={
                        "User-Agent": constants.USER_AGENT,
                        "Range": "bytes=0-0",
                    },
                    method="GET",
                )
                with _opener(self.proxy).open(request, timeout=5) as response:
                    if getattr(response, "status", 200) in (200, 206):
                        reachable.append(candidate)
                    else:
                        unreachable.append(candidate)
            except (urllib.error.URLError, ssl.SSLError, TimeoutError, OSError):
                unreachable.append(candidate)
        return reachable + unreachable

    def _download_one(
        self,
        component: ComponentId,
        candidate: UrlCandidate,
        part: Path,
        progress: ProgressCb | None,
        cancel: threading.Event | None,
    ) -> None:
        existing = part.stat().st_size if part.exists() else 0
        headers = {
            "User-Agent": constants.USER_AGENT,
            "Accept": "*/*",
        }
        if existing:
            headers["Range"] = f"bytes={existing}-"
        request = urllib.request.Request(candidate.url, headers=headers, method="GET")
        opener = _opener(self.proxy)
        with opener.open(request, timeout=self.read_timeout) as response:
            status = getattr(response, "status", 200)
            if status == 416:
                try:
                    part.unlink(missing_ok=True)
                except OSError:
                    pass
                raise DownloadError("续传位置无效，已重新开始")
            if existing and status == 200:
                existing = 0
                mode = "wb"
            else:
                mode = "ab" if existing else "wb"
            content_length = response.headers.get("Content-Length")
            total = None
            if content_length:
                try:
                    total = int(content_length) + (existing if status == 206 else 0)
                except ValueError:
                    total = None
            if total is None and existing:
                content_range = response.headers.get("Content-Range", "")
                if "/" in content_range:
                    try:
                        total = int(content_range.rsplit("/", 1)[1])
                    except ValueError:
                        total = None

            done = existing
            started = time.monotonic()
            last_report = 0.0
            with open(part, mode) as handle:
                while True:
                    if cancel and cancel.is_set():
                        raise DownloadCancelled()
                    try:
                        chunk = response.read(256 * 1024)
                    except (TimeoutError, OSError) as exc:
                        raise DownloadError(f"读取数据超时/失败: {exc}") from exc
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    now = time.monotonic()
                    if progress and (now - last_report >= 0.1 or (total and done >= total)):
                        elapsed = max(0.001, now - started)
                        speed = (done - existing) / elapsed
                        progress(component, done, total or 0, speed)
                        last_report = now
            if total and done < total:
                raise DownloadError(f"下载不完整（{done}/{total} 字节）")
            if progress:
                elapsed = max(0.001, time.monotonic() - started)
                progress(component, done, total or done, (done - existing) / elapsed)
