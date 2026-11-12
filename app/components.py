"""组件元数据、检测/远端结果合并与安装计划入口。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import config as config_module
from . import detect, install, remote
from .models import (
    Arch,
    ComponentId,
    DetectionResult,
    InstallPlan,
    ReleaseInfo,
    Requirement,
    Status,
)
from .versioning import is_newer, is_outdated

INSTALL_ORDER: tuple[ComponentId, ...] = (
    ComponentId.GIT,
    ComponentId.NODE,
    ComponentId.PYTHON,
    ComponentId.VSCODE,
    ComponentId.CLAUDE,
)

DISPLAY_ORDER: tuple[ComponentId, ...] = (
    ComponentId.CLAUDE,
    ComponentId.GIT,
    ComponentId.NODE,
    ComponentId.PYTHON,
    ComponentId.VSCODE,
)


@dataclass(frozen=True)
class ComponentSpec:
    id: ComponentId
    display_name: str
    requirement: Requirement
    min_version: tuple[int, ...] | None
    supports_custom_dir: bool
    detect: Callable[[], DetectionResult]


SPECS: dict[ComponentId, ComponentSpec] = {
    ComponentId.CLAUDE: ComponentSpec(
        ComponentId.CLAUDE, "Claude Code", Requirement.REQUIRED, None, False, detect.detect_claude
    ),
    ComponentId.GIT: ComponentSpec(
        ComponentId.GIT, "Git for Windows", Requirement.RECOMMENDED, None, True, detect.detect_git
    ),
    ComponentId.NODE: ComponentSpec(
        ComponentId.NODE, "Node.js (LTS 22+)", Requirement.RECOMMENDED, (22, 0, 0), True, detect.detect_node
    ),
    ComponentId.PYTHON: ComponentSpec(
        ComponentId.PYTHON, "Python", Requirement.OPTIONAL, None, True, detect.detect_python
    ),
    ComponentId.VSCODE: ComponentSpec(
        ComponentId.VSCODE, "VS Code", Requirement.OPTIONAL, None, True, detect.detect_vscode
    ),
}


def requirements() -> dict[ComponentId, Requirement]:
    return {cid: spec.requirement for cid, spec in SPECS.items()}


def custom_dir_components() -> tuple[ComponentId, ...]:
    return tuple(cid for cid, spec in SPECS.items() if spec.supports_custom_dir)


def apply_release(result: DetectionResult, release: ReleaseInfo | None) -> DetectionResult:
    if result.status in (Status.MISSING, Status.UNKNOWN, Status.ERROR):
        return result
    spec = SPECS[result.component]
    if result.version_tuple and is_outdated(result.version_tuple, spec.min_version):
        return result.with_status(Status.OUTDATED)
    if (
        release is not None
        and release.version_tuple
        and result.version_tuple
        and is_newer(result.version_tuple, release.version_tuple)
    ):
        return result.with_status(Status.UPDATE_AVAILABLE)
    if release is None:
        return result
    return result.with_status(Status.OK)


def install_dir_for(
    cid: ComponentId,
    config: config_module.AppConfig,
    detection: DetectionResult | None,
) -> str:
    if not SPECS[cid].supports_custom_dir:
        return ""
    if (
        detection is not None
        and detection.location
        and detection.status in (Status.OUTDATED, Status.UPDATE_AVAILABLE)
    ):
        # 升级必须原地进行，避免迁移目录造成残留。
        return detection.location
    detected = detection.location if detection and detection.location else None
    return config.install_dir_for(cid, detected)


def python_all_users(
    config: config_module.AppConfig,
    install_dir: str,
    detection: DetectionResult | None = None,
) -> bool:
    if config.install_options.python_all_users:
        return True
    if detection is not None and detection.scope == "all_users":
        return True
    target = str(install_dir).lower()
    program_files = str(config_module.constants.program_files()).lower()
    if target.startswith(program_files) or target.startswith(r"c:\program files"):
        return True
    if install_dir and not config_module.is_writable_directory(install_dir):
        return True
    return False


def build_install_plan(
    cid: ComponentId,
    release: ReleaseInfo,
    config: config_module.AppConfig,
    downloaded: str | Path | None,
    install_dir: str | Path,
    log_path: str | None,
    python_all_users: bool | None = None,
    prefer_npm: bool = False,
) -> InstallPlan:
    return install.build_plan(
        cid,
        release,
        config,
        downloaded,
        install_dir,
        log_path,
        python_all_users=python_all_users,
        prefer_npm=prefer_npm,
    )


def detect_mock() -> dict[ComponentId, DetectionResult]:
    return detect.mock_results()
