"""静默安装：命令构建、提权执行、退出码与 PATH 刷新。"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import shutil
import subprocess
import time
from pathlib import Path
from threading import Event

from . import constants, winreg_utils
from .models import CommandStep, ComponentId, InstallPlan, InstallResult, ReleaseInfo

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_NOASYNC = 0x00000100
SW_HIDE = 0
WAIT_TIMEOUT = 0x00000102
INFINITE = 0xFFFFFFFF
WINGET_ALREADY_INSTALLED = {0x8A15002B}
MSI_REBOOT_CODES = {3010, 1641}
INNO_REBOOT_CODES = {3010, 1641}


class _ShellExecuteInfoW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIcon", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def quote_path(path: str | Path) -> str:
    return f'"{path}"'


def msi_prop(name: str, value: str | Path) -> str:
    return f'{name}="{value}"'


def inno_dir(path: str | Path) -> str:
    return f'/DIR="{path}"'


def command_line(exe: str | Path, parameters: str) -> str:
    text = str(exe)
    exe_part = f'"{text}"' if " " in text else text
    return f"{exe_part} {parameters}".strip()


def _ensure_log_dir(log_path: str | None) -> str | None:
    if not log_path:
        return None
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return log_path


def build_node_plan(
    release: ReleaseInfo,
    downloaded: str | Path,
    install_dir: str | Path,
    log_path: str | None,
) -> InstallPlan:
    parameters = (
        f'/i {quote_path(downloaded)} /qn /norestart '
        f'{msi_prop("INSTALLDIR", install_dir)} /L*v {quote_path(log_path or "node-msi.log")}'
    )
    step = CommandStep(
        exe="msiexec.exe",
        parameters=parameters,
        needs_admin=True,
        label=f"安装 Node.js {release.version}",
        success_codes=frozenset(MSI_REBOOT_CODES | {0}),
        log_path=_ensure_log_dir(log_path),
    )
    return InstallPlan(ComponentId.NODE, (step,))


def build_git_plan(
    release: ReleaseInfo,
    downloaded: str | Path,
    install_dir: str | Path,
    log_path: str | None,
) -> InstallPlan:
    parameters = (
        "/VERYSILENT /NORESTART /NOCANCEL /SP- /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS "
        f"/LOG={quote_path(log_path or 'git-install.log')} {inno_dir(install_dir)}"
    )
    step = CommandStep(
        exe=str(downloaded),
        parameters=parameters,
        needs_admin=True,
        label=f"安装 Git for Windows {release.version}",
        success_codes=frozenset(INNO_REBOOT_CODES | {0}),
        log_path=_ensure_log_dir(log_path),
    )
    return InstallPlan(ComponentId.GIT, (step,))


def build_python_plan(
    release: ReleaseInfo,
    downloaded: str | Path,
    install_dir: str | Path,
    log_path: str | None,
    all_users: bool,
) -> InstallPlan:
    all_users_flag = 1 if all_users else 0
    parameters = (
        f"/quiet InstallAllUsers={all_users_flag} PrependPath=1 Include_test=0 "
        f"Include_launcher=1 InstallLauncherAllUsers={all_users_flag} "
        f"TargetDir={quote_path(install_dir)} /log {quote_path(log_path or 'python-install.log')}"
    )
    step = CommandStep(
        exe=str(downloaded),
        parameters=parameters,
        needs_admin=all_users,
        label=f"安装 Python {release.version}",
        success_codes=frozenset(MSI_REBOOT_CODES | {0}),
        log_path=_ensure_log_dir(log_path),
    )
    return InstallPlan(ComponentId.PYTHON, (step,))


def build_vscode_plan(
    release: ReleaseInfo,
    downloaded: str | Path,
    install_dir: str | Path,
    log_path: str | None,
) -> InstallPlan:
    parameters = (
        "/VERYSILENT /NORESTART /SP- /MERGETASKS=!runcode,addtopath "
        f"/LOG={quote_path(log_path or 'vscode-install.log')} {inno_dir(install_dir)}"
    )
    step = CommandStep(
        exe=str(downloaded),
        parameters=parameters,
        needs_admin=False,
        label=f"安装 VS Code {release.version}",
        success_codes=frozenset({0, 3010, 1641}),
        log_path=_ensure_log_dir(log_path),
    )
    return InstallPlan(ComponentId.VSCODE, (step,))


def _npm_steps(log_path: str | None) -> list[CommandStep]:
    npm = shutil.which("npm")
    if not npm:
        return []
    comspec = os.environ.get("COMSPEC", "cmd.exe")
    steps: list[CommandStep] = []
    for label, suffix in (
        ("使用 npm 安装 Claude Code", ""),
        ("使用 npm（npmmirror 镜像）安装 Claude Code", f" --registry={constants.NPM_MIRROR_REGISTRY}"),
    ):
        inner = f'"{npm}" install -g @anthropic-ai/claude-code{suffix}'
        steps.append(
            CommandStep(
                exe=comspec,
                parameters=f'/d /s /c "{inner}"',
                needs_admin=False,
                label=label,
                success_codes=frozenset({0}),
                log_path=_ensure_log_dir(log_path),
            )
        )
    return steps


def build_claude_plan(log_path: str | None, prefer_npm: bool = False) -> InstallPlan:
    winget = shutil.which("winget")
    winget_step = None
    if winget:
        winget_step = CommandStep(
            exe=winget,
            parameters=(
                f"install --id {constants.WINGET_CLAUDE_ID} -e --source {constants.WINGET_DOWNLOAD_SOURCE} "
                "--silent --accept-package-agreements --accept-source-agreements --disable-interactivity"
            ),
            needs_admin=False,
            label="使用 winget 安装 Claude Code",
            success_codes=frozenset({0} | WINGET_ALREADY_INSTALLED),
            log_path=_ensure_log_dir(log_path),
        )
    npm_steps = tuple(_npm_steps(log_path))
    steps: tuple[CommandStep, ...] = ()
    fallbacks: tuple[CommandStep, ...] = ()
    if prefer_npm and npm_steps:
        # 已安装版本可能来自 npm，且 npm 版本通常比 winget 新；升级时优先 npm，避免降级。
        steps = npm_steps
        fallbacks = (winget_step,) if winget_step else ()
    elif winget_step:
        steps = (winget_step,)
        fallbacks = npm_steps
    elif npm_steps:
        steps = npm_steps
    return InstallPlan(ComponentId.CLAUDE, steps, fallbacks, requires_download=False)


def build_plan(
    cid: ComponentId,
    release: ReleaseInfo,
    config,
    downloaded: str | Path | None,
    install_dir: str | Path,
    log_path: str | None,
    python_all_users: bool | None = None,
    prefer_npm: bool = False,
) -> InstallPlan:
    if cid == ComponentId.CLAUDE:
        return build_claude_plan(log_path, prefer_npm=prefer_npm)
    if downloaded is None:
        raise ValueError("缺少已下载的安装包")
    if cid == ComponentId.NODE:
        return build_node_plan(release, downloaded, install_dir, log_path)
    if cid == ComponentId.GIT:
        return build_git_plan(release, downloaded, install_dir, log_path)
    if cid == ComponentId.PYTHON:
        all_users = (
            bool(python_all_users)
            if python_all_users is not None
            else bool(getattr(config.install_options, "python_all_users", False))
        )
        return build_python_plan(release, downloaded, install_dir, log_path, all_users)
    if cid == ComponentId.VSCODE:
        return build_vscode_plan(release, downloaded, install_dir, log_path)
    raise ValueError(f"未知组件: {cid}")


def _require_admin() -> bool:
    return winreg_utils.is_admin()


def shell_execute_runas(
    exe: str,
    parameters: str,
    cancel: Event | None = None,
    timeout_s: int = 0,
    verb: str = "runas",
) -> tuple[int | None, str]:
    """以管理员身份运行安装器，返回 (exit_code, message)。"""
    info = _ShellExecuteInfoW()
    info.cbSize = ctypes.sizeof(_ShellExecuteInfoW)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb = verb
    info.lpFile = str(exe)
    info.lpParameters = parameters
    info.lpDirectory = None
    info.nShow = SW_HIDE
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfoW)]
        shell32.ShellExecuteExW.restype = wintypes.BOOL
        ok = shell32.ShellExecuteExW(ctypes.byref(info))
    except (AttributeError, OSError) as exc:
        return None, f"无法请求管理员权限: {exc}"
    if not ok or not info.hProcess:
        error = ctypes.get_last_error()
        if error == 1223:
            return None, "已取消管理员授权，未进行安装"
        return None, f"启动安装器失败（错误码 {error}）"

    started = time.monotonic()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    try:
        while True:
            result = kernel32.WaitForSingleObject(info.hProcess, 200)
            if result != WAIT_TIMEOUT:
                break
            if cancel is not None and cancel.is_set():
                return None, "已请求取消，安装器可能仍在运行"
            if timeout_s and time.monotonic() - started > timeout_s:
                kernel32.TerminateProcess(info.hProcess, 1)
                return None, "安装超时，已尝试终止安装器"
        exit_code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code))
        return int(exit_code.value), ""
    finally:
        kernel32.CloseHandle(info.hProcess)


def _write_log_header(log_path: str | None, command: str) -> None:
    if not log_path:
        return
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            handle.write(command + "\n")
    except OSError:
        pass


def _run_unelevated(
    step: CommandStep,
    cancel: Event | None,
    log,
) -> tuple[int | None, str]:
    command = command_line(step.exe, step.parameters)
    log_path = step.log_path
    stdout = subprocess.DEVNULL
    log_handle = None
    if log_path:
        try:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            log_handle = open(log_path, "a", encoding="utf-8", errors="replace")
            stdout = log_handle
        except OSError:
            stdout = subprocess.DEVNULL
    try:
        process = subprocess.Popen(
            command,
            stdout=stdout,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW,
        )
        started = time.monotonic()
        while True:
            code = process.poll()
            if code is not None:
                return code, ""
            if cancel is not None and cancel.is_set():
                try:
                    process.terminate()
                except OSError:
                    pass
                return None, "已取消安装"
            if step.timeout_s and time.monotonic() - started > step.timeout_s:
                try:
                    process.terminate()
                except OSError:
                    pass
                return None, "安装超时，已尝试终止安装器"
            time.sleep(0.3)
    except OSError as exc:
        return None, f"启动安装器失败: {exc}"
    finally:
        if log_handle is not None:
            try:
                log_handle.close()
            except OSError:
                pass


def _interpret_exit(step: CommandStep, code: int | None, message: str) -> InstallResult:
    if code is None:
        cancelled = "取消" in message
        return InstallResult(
            False,
            None,
            message or "安装未完成",
            log_path=step.log_path,
            cancelled=cancelled,
        )
    if code in step.success_codes:
        needs_reboot = code in (3010, 1641)
        text = "安装完成" if not needs_reboot else "安装完成，需要重启后完全生效"
        return InstallResult(True, code, text, needs_reboot=needs_reboot, log_path=step.log_path)
    if code in WINGET_ALREADY_INSTALLED:
        return InstallResult(True, code, "已安装或无需更新", log_path=step.log_path)
    return InstallResult(
        False,
        code,
        f"{step.label}失败（退出码 {code}，0x{code & 0xFFFFFFFF:08X}）",
        log_path=step.log_path,
    )


def run_step(step: CommandStep, cancel: Event | None, dry_run: bool, log) -> InstallResult:
    command = command_line(step.exe, step.parameters)
    log.info("执行: %s", command)
    _write_log_header(step.log_path, command)
    if dry_run:
        return InstallResult(True, 0, "预览模式，未实际安装", log_path=step.log_path)
    if step.needs_admin and not _require_admin():
        code, message = shell_execute_runas(step.exe, step.parameters, cancel)
        return _interpret_exit(step, code, message)
    code, message = _run_unelevated(step, cancel, log)
    return _interpret_exit(step, code, message)


def run_plan(plan: InstallPlan, cancel: Event | None, dry_run: bool, log) -> InstallResult:
    if not plan.steps:
        return InstallResult(False, None, "没有可用的安装方式（缺少 winget/npm）")
    last = InstallResult(False, None, "安装未执行")
    for step in plan.steps:
        last = run_step(step, cancel, dry_run, log)
        if last.success:
            return last
        if last.cancelled:
            return last
        log.warning("%s 失败：%s", step.label, last.message)
    for step in plan.fallbacks:
        log.info("尝试兜底方式：%s", step.label)
        last = run_step(step, cancel, dry_run, log)
        if last.success:
            return last
        log.warning("%s 失败：%s", step.label, last.message)
    return last


def refresh_environment(extra_dirs: list[str | Path] | None = None) -> None:
    winreg_utils.refresh_process_path(extra_dirs)


def running_processes(names: list[str]) -> list[str]:
    """返回正在运行的目标进程名（用于升级前提醒）。"""
    result: list[str] = []
    for name in names:
        try:
            completed = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                capture_output=True,
                timeout=8,
                check=False,
                creationflags=CREATE_NO_WINDOW,
            )
            text = completed.stdout.decode("utf-8", errors="replace").lower()
            if name.lower() in text and ".exe" in text:
                result.append(name)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return result
