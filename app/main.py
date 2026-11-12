"""程序入口：CLI 诊断模式与 GUI 启动。"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
from pathlib import Path

from . import constants
from .config import AppConfig
from .logging_setup import setup_logging
from .models import ComponentId, Status


def _set_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwarenessContext(-4)
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def _reconfigure_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=constants.APP_NAME,
        description="Claude Code 环境检测与安装工具",
    )
    parser.add_argument("--version", action="version", version=f"{constants.APP_NAME} {constants.APP_VERSION}")
    parser.add_argument("--detect-json", action="store_true", help="仅检测并输出 JSON 后退出")
    parser.add_argument("--out", type=str, default="", help="与 --detect-json / --print-plan 配合，写入文件")
    parser.add_argument("--print-plan", action="store_true", help="打印下载/安装计划后退出，不执行")
    parser.add_argument("--dry-run", action="store_true", help="GUI 预览模式：只记录命令，不下载不安装")
    parser.add_argument("--mock", action="store_true", help="使用模拟检测结果（用于演示/测试）")
    parser.add_argument("--no-network", action="store_true", help="跳过远端请求，使用内置版本")
    parser.add_argument("--download-only", action="store_true", help="仅下载，不安装")
    parser.add_argument(
        "--source",
        choices=("auto", "official", "mirror"),
        default=None,
        help="下载源策略",
    )
    parser.add_argument("--config", type=str, default="", help="指定配置文件路径")
    parser.add_argument("--debug", action="store_true", help="输出调试日志")
    return parser


def apply_cli_overrides(config: AppConfig, args) -> None:
    if args.source:
        config.network.mode = args.source


def _component_ready(status: Status) -> bool:
    return status in (Status.OK, Status.UPDATE_AVAILABLE)


def command_detect_json(config: AppConfig, args) -> int:
    from . import components, detect, remote

    local = detect.detect_all(mock=args.mock)
    system = detect.get_system_info()
    releases = remote.resolve_all(
        system.arch, config, use_network=not args.no_network
    )
    payload = {
        "app": constants.APP_NAME,
        "version": constants.APP_VERSION,
        "system": {
            "os": system.os_name,
            "version": system.os_version,
            "build": system.build,
            "arch": system.arch.value,
            "ram_gb": system.ram_gb,
            "supported": system.supported,
            "warnings": list(system.warnings),
        },
        "components": [],
    }
    for cid in components.DISPLAY_ORDER:
        result = components.apply_release(local[cid], releases.get(cid))
        release = releases.get(cid)
        payload["components"].append(
            {
                "id": cid.value,
                "name": components.SPECS[cid].display_name,
                "requirement": components.SPECS[cid].requirement.value,
                "status": result.status.value,
                "installed_version": result.installed_version,
                "latest_version": release.version if release else None,
                "location": result.location,
                "detected_by": result.detected_by,
                "notes": list(result.notes),
                "error": result.error,
            }
        )
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    elif sys.stdout is not None:
        try:
            print(text)
        except (OSError, ValueError, AttributeError):
            pass
    claude = next(item for item in payload["components"] if item["id"] == ComponentId.CLAUDE.value)
    return 0 if claude["status"] in (Status.OK.value, Status.UPDATE_AVAILABLE.value) else 1


def command_print_plan(config: AppConfig, args) -> int:
    from . import components, detect, install, remote

    system = detect.get_system_info()
    local = detect.detect_all(mock=args.mock)
    releases = remote.resolve_all(system.arch, config, use_network=not args.no_network)
    lines: list[str] = []
    lines.append(f"{constants.APP_NAME} {constants.APP_VERSION}")
    lines.append(f"系统: {system.os_name} {system.os_version} {system.arch.value}")
    lines.append(f"下载目录: {config.download_dir}")
    lines.append("")
    for cid in components.DISPLAY_ORDER:
        result = components.apply_release(local[cid], releases.get(cid))
        release = releases.get(cid)
        lines.append(
            f"[{cid.value}] {components.SPECS[cid].display_name}: "
            f"{result.status.value} 已安装={result.installed_version or '-'} "
            f"最新={release.version if release else '-'}"
        )
        if result.status == Status.OK and not args.dry_run:
            lines.append("  （已安装，默认跳过）")
        if release is None:
            lines.append("  无法获取版本信息")
            continue
        downloaded = Path(config.download_dir) / release.filename if release.filename else None
        install_dir = components.install_dir_for(cid, config, result)
        if cid == ComponentId.PYTHON:
            all_users = components.python_all_users(config, install_dir, result)
        else:
            all_users = None
        prefer_npm = bool(
            cid == ComponentId.CLAUDE
            and result.status == Status.UPDATE_AVAILABLE
        )
        try:
            plan = components.build_install_plan(
                cid,
                release,
                config,
                downloaded,
                install_dir or config.download_dir,
                str(constants.log_dir() / f"install-{cid.value}.log"),
                python_all_users=all_users,
                prefer_npm=prefer_npm,
            )
        except (ValueError, OSError) as exc:
            lines.append(f"  无法生成安装计划: {exc}")
            continue
        for step in plan.steps:
            lines.append("  " + install.command_line(step.exe, step.parameters))
        for step in plan.fallbacks:
            lines.append("  兜底: " + install.command_line(step.exe, step.parameters))
    text = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    elif sys.stdout is not None:
        try:
            print(text, end="")
        except (OSError, ValueError, AttributeError):
            pass
    return 0


def run_gui(config: AppConfig, args) -> int:
    _set_dpi_awareness()
    from queue import Queue

    from .controller import Controller
    from .logging_setup import attach_queue_handler
    from .models import UiEvent
    from .ui.window import MainWindow

    events: Queue[UiEvent] = Queue()
    attach_queue_handler(events)
    controller = Controller(config, events)
    window = MainWindow(
        config,
        controller,
        events,
        config_path=Path(args.config) if args.config else constants.config_path(),
        mock=args.mock,
        use_network=not args.no_network,
        dry_run=args.dry_run,
        download_only_default=args.download_only,
    )
    window.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    _reconfigure_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(debug=args.debug)
    config = AppConfig.load(Path(args.config) if args.config else None)
    apply_cli_overrides(config, args)
    if args.detect_json:
        return command_detect_json(config, args)
    if args.print_plan:
        return command_print_plan(config, args)
    return run_gui(config, args)


if __name__ == "__main__":
    raise SystemExit(main())
