"""配置持久化与路径校验。"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import constants
from .models import ComponentId

INSTALLABLE_IDS = (
    ComponentId.GIT,
    ComponentId.NODE,
    ComponentId.PYTHON,
    ComponentId.VSCODE,
)


@dataclass
class InstallOptions:
    python_all_users: bool = False
    allow_upgrade: bool = False
    download_only: bool = False


@dataclass
class NetworkOptions:
    mode: str = "auto"  # auto / official / mirror
    connect_timeout_s: int = constants.CONNECT_TIMEOUT
    read_timeout_s: int = constants.READ_TIMEOUT
    retries: int = constants.HTTP_RETRIES
    proxy: str | None = None
    cache_ttl_hours: int = 6


@dataclass
class AppConfig:
    schema_version: int = 1
    download_dir: str = ""
    install_dirs: dict[str, str] = field(
        default_factory=lambda: {cid.value: "" for cid in INSTALLABLE_IDS}
    )
    python_version: str = "3.15"
    install_options: InstallOptions = field(default_factory=InstallOptions)
    network: NetworkOptions = field(default_factory=NetworkOptions)

    def __post_init__(self) -> None:
        if not self.download_dir:
            self.download_dir = str(constants.default_download_dir())
        for cid in INSTALLABLE_IDS:
            self.install_dirs.setdefault(cid.value, "")

    def install_dir_for(self, cid: ComponentId, detected: str | None = None) -> str:
        configured = (self.install_dirs.get(cid.value) or "").strip()
        if configured:
            return configured
        if detected:
            return detected
        return str(default_install_dir(cid, self.python_version))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        if not isinstance(data, dict):
            return cls()
        known = {
            "schema_version",
            "download_dir",
            "install_dirs",
            "python_version",
            "install_options",
            "network",
        }
        values = {key: data[key] for key in known if key in data}
        options = values.get("install_options")
        if isinstance(options, dict):
            values["install_options"] = InstallOptions(
                python_all_users=bool(options.get("python_all_users", False)),
                allow_upgrade=bool(options.get("allow_upgrade", False)),
                download_only=bool(options.get("download_only", False)),
            )
        else:
            values.pop("install_options", None)
        network = values.get("network")
        if isinstance(network, dict):
            mode = str(network.get("mode", "auto"))
            if mode not in ("auto", "official", "mirror"):
                mode = "auto"
            values["network"] = NetworkOptions(
                mode=mode,
                connect_timeout_s=int(network.get("connect_timeout_s", constants.CONNECT_TIMEOUT)),
                read_timeout_s=int(network.get("read_timeout_s", constants.READ_TIMEOUT)),
                retries=int(network.get("retries", constants.HTTP_RETRIES)),
                proxy=network.get("proxy") or None,
                cache_ttl_hours=int(network.get("cache_ttl_hours", 6)),
            )
        else:
            values.pop("network", None)
        dirs = values.get("install_dirs")
        if isinstance(dirs, dict):
            values["install_dirs"] = {
                cid.value: str(dirs.get(cid.value, "") or "") for cid in INSTALLABLE_IDS
            }
        else:
            values.pop("install_dirs", None)
        if "download_dir" in values:
            values["download_dir"] = str(values["download_dir"] or "")
        if "python_version" in values:
            values["python_version"] = str(values["python_version"] or "3.15")
        if "schema_version" in values:
            try:
                values["schema_version"] = int(values["schema_version"])
            except (TypeError, ValueError):
                values.pop("schema_version", None)
        return cls(**values)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "AppConfig":
        target = Path(path) if path else constants.config_path()
        if not target.exists():
            return cls()
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            return cls.from_dict(data)
        except (OSError, ValueError, TypeError):
            try:
                backup = target.with_name(target.name + ".bad")
                target.replace(backup)
            except OSError:
                pass
            return cls()

    def save(self, path: str | Path | None = None) -> None:
        target = Path(path) if path else constants.config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        temp = target.with_name(target.name + ".tmp")
        temp.write_text(payload, encoding="utf-8")
        os.replace(temp, target)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    message: str = ""
    warning: str = ""


def default_install_dir(cid: ComponentId, python_version: str = "3.15") -> Path:
    if cid == ComponentId.GIT:
        return constants.program_files() / "Git"
    if cid == ComponentId.NODE:
        return constants.program_files() / "nodejs"
    if cid == ComponentId.VSCODE:
        return constants.local_programs() / "Microsoft VS Code"
    if cid == ComponentId.PYTHON:
        digits = "".join(ch for ch in python_version if ch.isdigit())[:3] or "315"
        return constants.local_programs() / "Python" / f"Python{digits}"
    return Path("")


def _existing_ancestor(path: Path) -> Path | None:
    current = path
    while True:
        if current.exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _is_writable(path: Path) -> bool:
    ancestor = _existing_ancestor(path)
    if ancestor is None:
        return False
    if not ancestor.is_dir():
        return False
    try:
        return os.access(ancestor, os.W_OK)
    except OSError:
        return False


def is_writable_directory(path: str | Path) -> bool:
    return _is_writable(Path(path))


def validate_directory(
    value: str | Path,
    cid: ComponentId | None = None,
    for_install: bool = True,
    require_writable: bool = True,
) -> ValidationResult:
    text = str(value or "").strip()
    if not text:
        return ValidationResult(False, "目录不能为空")
    if not os.path.isabs(text):
        return ValidationResult(False, "请输入绝对路径，例如 D:\\Tools\\Node.js")
    illegal = set('<>"|?*')
    if any(ch in illegal for ch in text):
        return ValidationResult(False, '路径不能包含 < > " | ? * 等字符')
    if text.endswith(" ") or text.endswith("."):
        return ValidationResult(False, "路径不能以空格或点结尾")
    path = Path(text)
    if path.parent == path:
        return ValidationResult(False, "不能安装到磁盘根目录")
    if path.exists() and not path.is_dir():
        return ValidationResult(False, "目标路径已存在且不是目录")
    if require_writable and not _is_writable(path):
        return ValidationResult(False, "目标目录不可写，请选择有写权限的位置")

    warning = ""
    if for_install:
        lowered = str(path).lower()
        program_files = str(constants.program_files()).lower()
        if cid in (ComponentId.PYTHON, ComponentId.VSCODE) and (
            lowered.startswith(program_files) or lowered.startswith(r"c:\program files")
        ):
            warning = "该位置通常需要管理员权限；Python/VS Code 默认按当前用户安装。"
        if path.exists():
            try:
                if any(path.iterdir()):
                    warning = "目标目录非空，安装器可能覆盖或与之混用。"
            except OSError:
                pass
    return ValidationResult(True, "", warning)


def ensure_directory(path: str | Path) -> Path:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target
