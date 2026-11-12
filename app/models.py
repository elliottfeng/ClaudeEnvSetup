"""数据模型、枚举与 UI 事件。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ComponentId(str, Enum):
    CLAUDE = "claude"
    GIT = "git"
    NODE = "node"
    PYTHON = "python"
    VSCODE = "vscode"


class Requirement(str, Enum):
    REQUIRED = "required"
    RECOMMENDED = "recommended"
    OPTIONAL = "optional"


class Status(str, Enum):
    MISSING = "missing"
    OUTDATED = "outdated"
    UPDATE_AVAILABLE = "update_available"
    OK = "ok"
    UNKNOWN = "unknown"
    ERROR = "error"


class Arch(str, Enum):
    X64 = "x64"
    ARM64 = "arm64"


@dataclass(frozen=True)
class DetectionResult:
    component: ComponentId
    status: Status
    installed_version: str | None = None
    version_tuple: tuple[int, ...] | None = None
    location: str | None = None
    detected_by: str = ""
    can_upgrade: bool = False
    notes: tuple[str, ...] = ()
    error: str | None = None
    scope: str = ""  # Python: "all_users" / "user"

    def with_status(self, status: Status) -> "DetectionResult":
        return DetectionResult(
            component=self.component,
            status=status,
            installed_version=self.installed_version,
            version_tuple=self.version_tuple,
            location=self.location,
            detected_by=self.detected_by,
            can_upgrade=self.can_upgrade,
            notes=self.notes,
            error=self.error,
            scope=self.scope,
        )


@dataclass(frozen=True)
class UrlCandidate:
    url: str
    source: str  # "official" / "mirror"
    sha256: str | None = None


@dataclass(frozen=True)
class ReleaseInfo:
    component: ComponentId
    version: str
    version_tuple: tuple[int, ...]
    filename: str
    urls: tuple[UrlCandidate, ...] = ()
    is_pinned: bool = False
    note: str = ""
    size: int | None = None


@dataclass(frozen=True)
class CommandStep:
    exe: str
    parameters: str
    needs_admin: bool
    label: str
    success_codes: frozenset[int] = frozenset({0})
    log_path: str | None = None
    timeout_s: int = 0


@dataclass(frozen=True)
class InstallPlan:
    component: ComponentId
    steps: tuple[CommandStep, ...]
    fallbacks: tuple[CommandStep, ...] = ()
    requires_download: bool = True


@dataclass(frozen=True)
class InstallResult:
    success: bool
    exit_code: int | None = None
    message: str = ""
    needs_reboot: bool = False
    log_path: str | None = None
    cancelled: bool = False


@dataclass(frozen=True)
class SystemInfo:
    os_name: str
    os_version: str
    build: int
    arch: Arch
    ram_gb: float | None = None
    supported: bool = True
    warnings: tuple[str, ...] = ()


@dataclass
class UiEvent:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OverallState:
    required_ok: int
    required_total: int
    recommended_ok: int
    recommended_total: int
    optional_ok: int
    optional_total: int
    headline: str
    level: str  # "ready" / "basic" / "not_ready"

    @staticmethod
    def from_results(results: dict[ComponentId, DetectionResult], requirements: dict[ComponentId, Requirement]) -> "OverallState":
        counts = {
            Requirement.REQUIRED: [0, 0],
            Requirement.RECOMMENDED: [0, 0],
            Requirement.OPTIONAL: [0, 0],
        }
        for cid, req in requirements.items():
            result = results.get(cid)
            counts[req][1] += 1
            if result and result.status in (Status.OK, Status.UPDATE_AVAILABLE):
                counts[req][0] += 1
        required_ok, required_total = counts[Requirement.REQUIRED]
        recommended_ok, recommended_total = counts[Requirement.RECOMMENDED]
        optional_ok, optional_total = counts[Requirement.OPTIONAL]

        required_bad = required_ok < required_total
        recommended_bad = recommended_ok < recommended_total
        if required_bad:
            headline = "未就绪：请先安装 Claude Code"
            level = "not_ready"
        elif recommended_bad:
            headline = "基础可用：缺少推荐组件"
            level = "basic"
        else:
            headline = "环境就绪"
            level = "ready"
        return OverallState(
            required_ok=required_ok,
            required_total=required_total,
            recommended_ok=recommended_ok,
            recommended_total=recommended_total,
            optional_ok=optional_ok,
            optional_total=optional_total,
            headline=headline,
            level=level,
        )
