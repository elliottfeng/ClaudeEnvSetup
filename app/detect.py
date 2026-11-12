"""五个组件的本地检测与输出解析。"""

from __future__ import annotations

import ctypes
import glob
import json
import locale
import os
import platform
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import constants, winreg_utils
from .models import Arch, ComponentId, DetectionResult, Status, SystemInfo
from .versioning import is_outdated, parse_version

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NODE_MIN = (22, 0, 0)


def _decode(data: bytes | None) -> str:
    if not data:
        return ""
    encodings = ["utf-8", locale.getpreferredencoding(False), "gbk", "latin-1"]
    for encoding in dict.fromkeys(encodings):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def run_command(args: list[str], timeout: int = constants.CLI_TIMEOUT) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )
        return completed.returncode, _decode(completed.stdout), _decode(completed.stderr)
    except subprocess.TimeoutExpired:
        return -1, "", "命令执行超时"
    except OSError as exc:
        return -1, "", str(exc)


def run_command_string(command: str, timeout: int = constants.CLI_TIMEOUT) -> tuple[int, str, str]:
    """直接以字符串命令行调用（用于 cmd.exe 包装，避免引号被二次转义）。"""
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=timeout,
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )
        return completed.returncode, _decode(completed.stdout), _decode(completed.stderr)
    except subprocess.TimeoutExpired:
        return -1, "", "命令执行超时"
    except OSError as exc:
        return -1, "", str(exc)


def run_script(path: str | Path, args: list[str], timeout: int = constants.CLI_TIMEOUT) -> tuple[int, str, str]:
    """通过 cmd.exe 执行 .cmd/.bat；其余直接执行。"""
    target = str(path)
    suffix = Path(target).suffix.lower()
    if suffix in (".cmd", ".bat"):
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        argument_text = subprocess.list2cmdline(args)
        inner = f'"{target}"' + (f" {argument_text}" if argument_text else "")
        return run_command_string(f'{comspec} /d /s /c "{inner}"', timeout=timeout)
    return run_command([target, *args], timeout=timeout)


def which(name: str) -> str | None:
    return shutil.which(name)


def _env(*parts: str) -> Path:
    base = os.environ.get(parts[0])
    if not base:
        return Path.home().joinpath(*parts[1:]) if len(parts) > 1 else Path(parts[0])
    return Path(base).joinpath(*parts[1:])


def _candidate_paths(items: list[Path | str | None]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if not item:
            continue
        try:
            path = str(Path(item))
        except (TypeError, ValueError):
            continue
        lowered = path.lower()
        if lowered not in seen:
            seen.add(lowered)
            result.append(path)
    return result


def _first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists() and path.is_file():
            return path
    return None


def _normalized(path: str) -> str:
    if not path:
        return ""
    try:
        return os.path.normpath(os.path.abspath(path)).lower()
    except (OSError, ValueError):
        return path.lower()


def _unique_candidates(
    candidates: list[tuple[tuple[int, ...] | None, str, str]]
) -> list[tuple[tuple[int, ...], str, str]]:
    """按安装目录去重，保留同一目录下版本最高的候选。"""
    grouped: dict[str, tuple[tuple[int, ...], str, str]] = {}
    for version, exe, location in candidates:
        if not version:
            continue
        key = _normalized(location) or _normalized(exe)
        previous = grouped.get(key)
        if previous is None or version > previous[0]:
            grouped[key] = (version, exe, location)
    return sorted(grouped.values(), key=lambda item: item[0], reverse=True)


def _make_result(
    cid: ComponentId,
    version: tuple[int, ...] | None,
    location: str | None,
    detected_by: str,
    minimum: tuple[int, ...] | None = None,
    notes: tuple[str, ...] = (),
    version_text: str | None = None,
    scope: str = "",
) -> DetectionResult:
    if version is None:
        return DetectionResult(
            component=cid,
            status=Status.UNKNOWN,
            location=location,
            detected_by=detected_by,
            notes=notes,
            error="已找到安装位置，但无法解析版本号",
            scope=scope,
        )
    status = Status.OUTDATED if is_outdated(version, minimum) else Status.OK
    return DetectionResult(
        component=cid,
        status=status,
        installed_version=version_text or ".".join(str(part) for part in version),
        version_tuple=version,
        location=location,
        detected_by=detected_by,
        can_upgrade=status == Status.OUTDATED,
        notes=notes,
        scope=scope,
    )


def _missing(cid: ComponentId, notes: tuple[str, ...] = ()) -> DetectionResult:
    return DetectionResult(component=cid, status=Status.MISSING, notes=notes)


def parse_git_version(text: str) -> tuple[int, ...] | None:
    match = re.search(r"git version\s+([^\s]+)", text, re.IGNORECASE)
    return parse_version(match.group(1) if match else text)


def parse_node_version(text: str) -> tuple[int, ...] | None:
    match = re.search(r"v?(\d+\.\d+\.\d+)", text)
    return parse_version(match.group(1) if match else text)


def parse_claude_version(text: str) -> tuple[int, ...] | None:
    match = re.search(r"(\d+\.\d+\.\d+)", text)
    return parse_version(match.group(1) if match else text)


def parse_code_version(text: str) -> tuple[int, ...] | None:
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    return parse_version(first_line)


def parse_py_launcher_listing(text: str) -> list[tuple[tuple[int, ...], str]]:
    """解析 ``py -0p`` 输出：`` -V:3.15 *  C:\\Python315\\python.exe``。"""
    result: list[tuple[tuple[int, ...], str]] = []
    pattern = re.compile(r"-V:(\d+\.\d+)(?:\s+\*)?\s+(.+)$", re.IGNORECASE)
    for line in text.splitlines():
        match = pattern.search(line.strip())
        if not match:
            continue
        version = parse_version(match.group(1))
        path = match.group(2).strip().strip('"')
        if version and path:
            result.append((version, path))
    return result


def _run_version_for(path: str | Path) -> tuple[tuple[int, ...] | None, str, str]:
    code, out, err = run_script(path, ["--version"])
    text = (out or err).strip()
    if code != 0 and not text:
        return None, "", err
    return parse_version(text), text, err


def detect_node() -> DetectionResult:
    notes: list[str] = []
    candidates: list[tuple[tuple[int, ...] | None, str, str]] = []

    for root, wow64 in ((winreg_utils.hklm(), False), (winreg_utils.hkcu(), False)):
        install_path = winreg_utils.read_value_ex(root, r"SOFTWARE\Node.js", "InstallPath", wow64)
        registry_version = winreg_utils.read_value_ex(root, r"SOFTWARE\Node.js", "Version", wow64)
        if install_path:
            exe = Path(str(install_path)) / "node.exe"
            if exe.exists():
                version, text, _ = _run_version_for(exe)
                candidates.append((version or parse_version(str(registry_version)), str(exe), str(install_path)))
            elif registry_version:
                candidates.append((parse_version(str(registry_version)), "", str(install_path)))

    node_path = which("node")
    if node_path:
        version, _, _ = _run_version_for(node_path)
        candidates.append((version, node_path, str(Path(node_path).parent)))

    for exe in _candidate_paths(
        [
            _env("ProgramFiles", "nodejs", "node.exe"),
            _env("LOCALAPPDATA", "Programs", "nodejs", "node.exe"),
            _env("ProgramFiles(x86)", "nodejs", "node.exe"),
        ]
    ):
        if Path(exe).exists():
            version, _, _ = _run_version_for(exe)
            candidates.append((version, exe, str(Path(exe).parent)))

    valid = _unique_candidates(candidates)
    if not valid:
        return _missing(ComponentId.NODE)
    version, exe, location = valid[0]
    if len(valid) > 1:
        notes.append("检测到多个 Node.js 安装，已显示版本最高的一项")
    if version and is_outdated(version, NODE_MIN):
        notes.append("Node.js 低于 22，仅影响 npm 方式安装 Claude Code")
    return _make_result(ComponentId.NODE, version, location, "PATH/注册表", NODE_MIN, tuple(notes))


def _git_root_from_cli(path: str | Path) -> str:
    resolved = Path(path)
    for parent in [resolved.parent, resolved.parent.parent, resolved.parent.parent.parent]:
        if (parent / "cmd" / "git.exe").exists() or (parent / "bin" / "bash.exe").exists():
            return str(parent)
    return str(resolved.parent.parent)


def detect_git() -> DetectionResult:
    notes: list[str] = []
    candidates: list[tuple[tuple[int, ...] | None, str, str]] = []

    for root, wow64 in (
        (winreg_utils.hklm(), False),
        (winreg_utils.hkcu(), False),
        (winreg_utils.hklm(), True),
    ):
        install_path = winreg_utils.read_value_ex(root, r"SOFTWARE\GitForWindows", "InstallPath", wow64)
        registry_version = winreg_utils.read_value_ex(root, r"SOFTWARE\GitForWindows", "CurrentVersion", wow64)
        if install_path:
            exe = Path(str(install_path)) / "cmd" / "git.exe"
            if exe.exists():
                version, _, _ = _run_version_for(exe)
                candidates.append((version or parse_version(str(registry_version)), str(exe), str(install_path)))
            elif registry_version:
                candidates.append((parse_version(str(registry_version)), "", str(install_path)))

    git_path = which("git")
    if git_path:
        version, _, _ = _run_version_for(git_path)
        candidates.append((version, git_path, _git_root_from_cli(git_path)))

    for exe in _candidate_paths(
        [
            _env("ProgramFiles", "Git", "cmd", "git.exe"),
            _env("LOCALAPPDATA", "Programs", "Git", "cmd", "git.exe"),
            _env("ProgramFiles(x86)", "Git", "cmd", "git.exe"),
        ]
    ):
        if Path(exe).exists():
            version, _, _ = _run_version_for(exe)
            candidates.append((version, exe, _git_root_from_cli(exe)))

    valid = _unique_candidates(candidates)
    if not valid:
        return _missing(ComponentId.GIT)
    version, exe, location = valid[0]
    if len(valid) > 1:
        notes.append("检测到多个 Git 安装，已显示版本最高的一项")
    return _make_result(ComponentId.GIT, version, location, "PATH/注册表", None, tuple(notes))


def _collect_python_candidates() -> list[tuple[tuple[int, ...], str, str]]:
    candidates: list[tuple[tuple[int, ...], str, str]] = []
    for root, wow64 in (
        (winreg_utils.hklm(), False),
        (winreg_utils.hkcu(), False),
        (winreg_utils.hklm(), True),
    ):
        base = r"SOFTWARE\Python\PythonCore"
        for subkey in winreg_utils.enum_subkeys(root, base):
            key_path = f"{base}\\{subkey}"
            executable = winreg_utils.read_value_ex(root, key_path + r"\InstallPath", "ExecutablePath", wow64)
            install_path = winreg_utils.read_value_ex(root, key_path + r"\InstallPath", "", wow64)
            exe = Path(str(executable)) if executable else None
            if not exe or not exe.exists():
                if install_path:
                    candidate = Path(str(install_path)) / "python.exe"
                    exe = candidate if candidate.exists() else None
            if not exe:
                continue
            if "windowsapps" in str(exe).lower():
                continue
            version, _, _ = _run_version_for(exe)
            if version is None:
                version = parse_version(subkey)
            if version:
                candidates.append((version, str(exe), str(exe.parent)))

    code, out, _ = run_command(["py", "-0p"])
    if code == 0 and out:
        for version, exe_path in parse_py_launcher_listing(out):
            exe = Path(exe_path)
            if exe.exists() and "windowsapps" not in str(exe).lower():
                version2, _, _ = _run_version_for(exe)
                candidates.append((version2 or version, str(exe), str(exe.parent)))

    python_path = which("python")
    if python_path and "windowsapps" not in python_path.lower():
        version, _, _ = _run_version_for(python_path)
        candidates.append((version, python_path, str(Path(python_path).parent)))

    patterns = [
        str(_env("LOCALAPPDATA", "Programs", "Python")) + r"\Python3*\python.exe",
        r"C:\Python3*\python.exe",
    ]
    for pattern in patterns:
        for exe_path in glob.glob(pattern):
            if "windowsapps" in exe_path.lower():
                continue
            version, _, _ = _run_version_for(exe_path)
            candidates.append((version, exe_path, str(Path(exe_path).parent)))
    return candidates


def _python_scope(location: str) -> str:
    target = _normalized(location)
    base = r"SOFTWARE\Python\PythonCore"
    for root, label, wow64 in (
        (winreg_utils.hklm(), "all_users", False),
        (winreg_utils.hkcu(), "user", False),
        (winreg_utils.hklm(), "all_users", True),
    ):
        for subkey in winreg_utils.enum_subkeys(root, base):
            install = winreg_utils.read_value_ex(root, f"{base}\\{subkey}\\InstallPath", "", wow64)
            if install and _normalized(str(install)) == target:
                return label
    return ""


def detect_python() -> DetectionResult:
    ordered = _unique_candidates(_collect_python_candidates())
    if not ordered:
        return _missing(ComponentId.PYTHON)
    version, exe, location = ordered[0]
    notes: list[str] = []
    if len(ordered) > 1:
        notes.append("检测到多个 Python 版本，已显示最高版本")
    return _make_result(
        ComponentId.PYTHON,
        version,
        location,
        "PEP 514/CLI",
        None,
        tuple(notes),
        scope=_python_scope(location),
    )


def _vscode_root_from_cli(path: str) -> str:
    resolved = Path(path)
    if resolved.suffix.lower() in (".cmd", ".bat"):
        return str(resolved.parent.parent)
    return str(resolved.parent)


def detect_vscode() -> DetectionResult:
    notes: list[str] = []
    candidates: list[tuple[tuple[int, ...] | None, str, str]] = []

    code_path = which("code")
    if code_path:
        version, _, _ = _run_version_for(code_path)
        candidates.append((version, code_path, _vscode_root_from_cli(code_path)))

    for cmd in _candidate_paths(
        [
            _env("LOCALAPPDATA", "Programs", "Microsoft VS Code", "bin", "code.cmd"),
            _env("ProgramFiles", "Microsoft VS Code", "bin", "code.cmd"),
            _env("ProgramFiles(x86)", "Microsoft VS Code", "bin", "code.cmd"),
        ]
    ):
        if Path(cmd).exists():
            version, _, _ = _run_version_for(cmd)
            candidates.append((version, cmd, str(Path(cmd).parent.parent)))

    for entry in winreg_utils.find_uninstall_entries("Visual Studio Code"):
        version = parse_version(entry.get("display_version"))
        location = entry.get("install_location") or ""
        cmd = Path(location) / "bin" / "code.cmd" if location else None
        if cmd and cmd.exists():
            cli_version, _, _ = _run_version_for(cmd)
            candidates.append((cli_version or version, str(cmd), location))
        elif version:
            candidates.append((version, "", location))

    valid = _unique_candidates(candidates)
    if not valid:
        return _missing(ComponentId.VSCODE)
    version, exe, location = valid[0]
    if len(valid) > 1:
        notes.append("检测到多个 VS Code 安装（可能同时存在 User/System 版本）")
    return _make_result(ComponentId.VSCODE, version, location, "PATH/卸载项", None, tuple(notes))


def _npm_claude_version() -> tuple[tuple[int, ...] | None, str]:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None, ""
    package = Path(appdata) / "npm" / "node_modules" / "@anthropic-ai" / "claude-code" / "package.json"
    try:
        data = json.loads(package.read_text(encoding="utf-8"))
        return parse_version(str(data.get("version", ""))), str(package.parent)
    except (OSError, ValueError, TypeError):
        return None, ""


def _winget_claude_candidates() -> list[tuple[tuple[int, ...] | None, str]]:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []
    base = Path(local) / "Microsoft" / "WinGet" / "Packages"
    if not base.exists():
        return []
    result: list[tuple[tuple[int, ...] | None, str]] = []
    try:
        for package_dir in base.glob("Anthropic.ClaudeCode_*"):
            for exe in package_dir.rglob("claude.exe"):
                version, _, _ = _run_version_for(exe)
                result.append((version, str(exe)))
                break
    except OSError:
        pass
    return result


def detect_claude() -> DetectionResult:
    notes: list[str] = []
    candidates: list[tuple[tuple[int, ...] | None, str, str]] = []

    claude_path = which("claude")
    if claude_path:
        version, _, _ = _run_version_for(claude_path)
        candidates.append((version, claude_path, str(Path(claude_path).parent)))

    native = Path.home() / ".local" / "bin" / "claude.exe"
    if native.exists():
        version, _, _ = _run_version_for(native)
        candidates.append((version, str(native), str(native.parent)))

    npm_cmd = _env("APPDATA", "npm", "claude.cmd")
    if npm_cmd.exists():
        version, _, _ = _run_version_for(npm_cmd)
        npm_version, npm_location = _npm_claude_version()
        candidates.append((version or npm_version, str(npm_cmd), str(npm_cmd.parent)))

    windows_apps = _env("LOCALAPPDATA", "Microsoft", "WindowsApps", "claude.exe")
    if windows_apps.exists():
        version, _, _ = _run_version_for(windows_apps)
        if version:
            candidates.append((version, str(windows_apps), str(windows_apps.parent)))

    for version, exe in _winget_claude_candidates():
        candidates.append((version, exe, str(Path(exe).parent)))

    npm_version, npm_location = _npm_claude_version()
    if npm_version and not any(item[0] for item in candidates):
        candidates.append((npm_version, "", npm_location))

    valid = _unique_candidates(candidates)
    if not valid:
        return _missing(ComponentId.CLAUDE)
    version, exe, location = valid[0]
    if len(valid) > 1:
        notes.append("检测到多个 Claude Code 安装来源，已显示 PATH 命中的最高版本")
    return _make_result(ComponentId.CLAUDE, version, location, "PATH/npm/winget", None, tuple(notes))


def get_ram_gb() -> float | None:
    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return round(status.ullTotalPhys / (1024**3), 1)
    except (AttributeError, OSError):
        pass
    return None


def get_system_info() -> SystemInfo:
    version_text = platform.version()
    parts = version_text.split(".")
    try:
        build = int(parts[2]) if len(parts) >= 3 else 0
    except ValueError:
        build = 0
    machine = platform.machine().lower()
    arch = Arch.ARM64 if machine in ("arm64", "aarch64") else Arch.X64
    os_name = "Windows 11" if build >= 22000 else "Windows 10"
    ram = get_ram_gb()
    warnings: list[str] = []
    supported = winreg_utils.is_supported_windows(build)
    if not supported:
        warnings.append("Windows 版本低于 10.0.17763（1809），Claude Code 可能无法运行")
    if ram is not None and ram < 4:
        warnings.append(f"物理内存仅 {ram} GB，Claude Code 建议至少 4 GB")
    return SystemInfo(
        os_name=os_name,
        os_version=version_text,
        build=build,
        arch=arch,
        ram_gb=ram,
        supported=supported,
        warnings=tuple(warnings),
    )


def mock_results() -> dict[ComponentId, DetectionResult]:
    return {
        ComponentId.CLAUDE: _missing(ComponentId.CLAUDE),
        ComponentId.GIT: _make_result(
            ComponentId.GIT, (2, 56, 0), r"C:\Program Files\Git", "mock"
        ),
        ComponentId.NODE: _make_result(
            ComponentId.NODE,
            (20, 11, 0),
            r"C:\Program Files\nodejs",
            "mock",
            NODE_MIN,
            ("Node.js 低于 22，仅影响 npm 方式安装 Claude Code",),
        ),
        ComponentId.PYTHON: _make_result(
            ComponentId.PYTHON, (3, 15, 1), r"C:\Python315", "mock", scope="all_users"
        ),
        ComponentId.VSCODE: _make_result(
            ComponentId.VSCODE, (1, 145, 3), r"C:\Program Files\Microsoft VS Code", "mock"
        ),
    }


DETECTORS = {
    ComponentId.CLAUDE: detect_claude,
    ComponentId.GIT: detect_git,
    ComponentId.NODE: detect_node,
    ComponentId.PYTHON: detect_python,
    ComponentId.VSCODE: detect_vscode,
}


def detect_all(mock: bool = False) -> dict[ComponentId, DetectionResult]:
    if mock:
        return mock_results()
    results: dict[ComponentId, DetectionResult] = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {cid: pool.submit(detector) for cid, detector in DETECTORS.items()}
        for cid, future in futures.items():
            try:
                results[cid] = future.result()
            except Exception as exc:  # pragma: no cover - 防御性
                results[cid] = DetectionResult(
                    component=cid,
                    status=Status.ERROR,
                    error=f"检测失败: {exc}",
                )
    order = [ComponentId.CLAUDE, ComponentId.GIT, ComponentId.NODE, ComponentId.PYTHON, ComponentId.VSCODE]
    return {cid: results[cid] for cid in order}
