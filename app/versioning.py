"""版本号解析、比较与预发布判断。"""

from __future__ import annotations

import re

_VERSION_START = re.compile(r"^[vV]?(\d+(?:\.\d+){0,3})")
_VERSION_ANYWHERE = re.compile(r"[vV]?(\d+(?:\.\d+){1,3})")
_PRERELEASE = re.compile(
    r"(?i)(?:^|[^a-z])(alpha|beta|rc|dev|pre|preview|insider)(?:\d|[^a-z]|$)|(\d)(a|b|rc)(\d)"
)


def parse_version(text: str | None) -> tuple[int, ...] | None:
    """从任意版本输出中提取版本元组。

    支持 ``v24.22.1``、``git version 2.56.0.windows.3``、
    ``2.1.319 (Claude Code)``、``Python 3.15.1``。
    """
    if not text:
        return None
    cleaned = str(text).strip()
    if not cleaned:
        return None
    match = _VERSION_START.match(cleaned)
    if not match:
        match = _VERSION_ANYWHERE.search(cleaned)
    if not match:
        return None
    try:
        return tuple(int(part) for part in match.group(1).split("."))
    except (TypeError, ValueError):
        return None


def is_prerelease(text: str | None) -> bool:
    if not text:
        return False
    return bool(_PRERELEASE.search(str(text)))


def _padded(version: tuple[int, ...], width: int = 4) -> tuple[int, ...]:
    values = list(version[:width])
    values.extend([0] * (width - len(values)))
    return tuple(values)


def compare_versions(a: tuple[int, ...] | None, b: tuple[int, ...] | None) -> int:
    """返回 -1/0/1；None 视为最小。"""
    if a is None and b is None:
        return 0
    if a is None:
        return -1
    if b is None:
        return 1
    pa, pb = _padded(a), _padded(b)
    return (pa > pb) - (pa < pb)


def is_newer(installed: tuple[int, ...] | None, latest: tuple[int, ...] | None) -> bool:
    return compare_versions(installed, latest) < 0


def is_outdated(installed: tuple[int, ...] | None, minimum: tuple[int, ...] | None) -> bool:
    if minimum is None:
        return False
    return compare_versions(installed, minimum) < 0


def same_version(a: tuple[int, ...] | None, b: tuple[int, ...] | None) -> bool:
    return compare_versions(a, b) == 0


def format_version(version: tuple[int, ...] | str | None) -> str:
    if version is None:
        return ""
    if isinstance(version, str):
        return version
    return ".".join(str(part) for part in version)


def version_key(text: str) -> tuple[int, ...]:
    """用于排序的宽松解析；无法解析时返回空元组。"""
    return parse_version(text) or ()
