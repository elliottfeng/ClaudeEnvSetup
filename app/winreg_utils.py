"""Windows 注册表、PATH 刷新与管理员判断。"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

try:  # pragma: no cover - 仅 Windows
    import winreg
except ImportError:  # pragma: no cover
    winreg = None  # type: ignore[assignment]

HWND_BROADCAST = 0xFFFF
WM_SETTINGCHANGE = 0x001A
SMTO_ABORTIFHUNG = 0x0002


def available() -> bool:
    return winreg is not None


def _open(root, path: str, access: int | None = None):
    if winreg is None:
        return None
    if access is None:
        access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
    return winreg.OpenKey(root, path, 0, access)


def read_value(root, path: str, name: str = "") -> object | None:
    if winreg is None:
        return None
    try:
        with _open(root, path) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return value
    except OSError:
        return None


def read_string(root, path: str, name: str = "") -> str | None:
    value = read_value(root, path, name)
    return str(value) if value is not None else None


def enum_subkeys(root, path: str) -> list[str]:
    if winreg is None:
        return []
    result: list[str] = []
    try:
        with _open(root, path) as key:
            index = 0
            while True:
                try:
                    result.append(winreg.EnumKey(key, index))
                except OSError:
                    break
                index += 1
    except OSError:
        return []
    return result


def hklm():
    return winreg.HKEY_LOCAL_MACHINE if winreg else None


def hkcu():
    return winreg.HKEY_CURRENT_USER if winreg else None


def enum_wow64_roots():
    """返回 (根键, 是否 WOW64) 组合，覆盖 32/64 位注册表视图。"""
    if winreg is None:
        return []
    return [
        (winreg.HKEY_LOCAL_MACHINE, False),
        (winreg.HKEY_CURRENT_USER, False),
        (winreg.HKEY_LOCAL_MACHINE, True),
        (winreg.HKEY_CURRENT_USER, True),
    ]


def read_value_ex(root, path: str, name: str = "", wow64: bool = False) -> object | None:
    if winreg is None:
        return None
    access = winreg.KEY_READ
    if wow64:
        access |= getattr(winreg, "KEY_WOW64_32KEY", 0)
    else:
        access |= getattr(winreg, "KEY_WOW64_64KEY", 0)
    try:
        with winreg.OpenKey(root, path, 0, access) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return value
    except OSError:
        return None


def expand_environment_strings(value: str) -> str:
    if not value:
        return ""
    try:
        size = ctypes.windll.kernel32.ExpandEnvironmentStringsW(value, None, 0)
        if size:
            buffer = ctypes.create_unicode_buffer(size)
            ctypes.windll.kernel32.ExpandEnvironmentStringsW(value, buffer, size)
            return buffer.value
    except (AttributeError, OSError):
        pass
    return os.path.expandvars(value)


def read_environment_paths() -> tuple[str, str]:
    system_path = ""
    user_path = ""
    if winreg is not None:
        system_path = read_string(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            "Path",
        ) or ""
        user_path = read_string(winreg.HKEY_CURRENT_USER, r"Environment", "Path") or ""
    system_path = expand_environment_strings(system_path)
    user_path = expand_environment_strings(user_path)
    return system_path, user_path


def _split_path(value: str) -> list[str]:
    return [part.strip() for part in value.split(";") if part.strip()]


def refresh_process_path(extra_dirs: list[str | Path] | None = None) -> None:
    """从注册表重建 PATH，并前插本次安装目录。"""
    system_path, user_path = read_environment_paths()
    existing_path = os.environ.get("PATH", "")
    parts: list[str] = []
    for value in [
        *map(str, extra_dirs or []),
        *(system_path.split(";")),
        *(user_path.split(";")),
        *(existing_path.split(";")),
    ]:
        for part in _split_path(value):
            if part and part.lower() not in {p.lower() for p in parts}:
                parts.append(part)
    if parts:
        os.environ["PATH"] = ";".join(parts)
    broadcast_environment_change()


def broadcast_environment_change() -> None:
    try:
        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            "Environment",
            SMTO_ABORTIFHUNG,
            5000,
            ctypes.byref(result),
        )
    except (AttributeError, OSError):
        pass


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def is_supported_windows(build: int) -> bool:
    return build >= 17763


def common_env_paths() -> dict[str, str]:
    return {
        "program_files": os.environ.get("ProgramFiles", r"C:\Program Files"),
        "program_files_x86": os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        "local_app_data": os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")),
        "app_data": os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")),
        "user_profile": str(Path.home()),
    }


def iter_uninstall_roots():
    if winreg is None:
        return []
    return [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]


def find_uninstall_entries(name_fragment: str) -> list[dict[str, str]]:
    """按 DisplayName 模糊查找卸载项。"""
    result: list[dict[str, str]] = []
    needle = name_fragment.lower()
    for root, base in iter_uninstall_roots():
        for sub in enum_subkeys(root, base):
            path = f"{base}\\{sub}"
            display = read_string(root, path, "DisplayName") or ""
            if needle not in display.lower():
                continue
            result.append(
                {
                    "display_name": display,
                    "display_version": read_string(root, path, "DisplayVersion") or "",
                    "install_location": read_string(root, path, "InstallLocation") or "",
                    "uninstall_string": read_string(root, path, "UninstallString") or "",
                }
            )
    return result
