"""主窗口：组件表、路径设置、进度与日志。"""

from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from queue import Empty, Queue
from tkinter import filedialog, messagebox, ttk

from .. import components, config as config_module, constants, install, winreg_utils
from ..config import AppConfig
from ..controller import Controller
from ..models import (
    ComponentId,
    DetectionResult,
    InstallResult,
    OverallState,
    ReleaseInfo,
    Requirement,
    Status,
    SystemInfo,
    UiEvent,
)
from .dialogs import AdvancedSettingsDialog, ask_confirm, show_error
from .widgets import ComponentRow, shorten

GROUP_ORDER_DEPENDENCIES = (ComponentId.GIT, ComponentId.NODE, ComponentId.PYTHON, ComponentId.VSCODE)


def _fmt_bytes(value: int | None) -> str:
    if value is None:
        return "-"
    size = float(value)
    if size >= 1024**3:
        return f"{size / 1024**3:.2f} GB"
    if size >= 1024**2:
        return f"{size / 1024**2:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{int(size)} B"


def _fmt_speed(value: float) -> str:
    if not value:
        return "-"
    if value >= 1024**2:
        return f"{value / 1024**2:.1f} MB/s"
    if value >= 1024:
        return f"{value / 1024:.0f} KB/s"
    return f"{value:.0f} B/s"


class MainWindow:
    def __init__(
        self,
        config: AppConfig,
        controller: Controller,
        events: Queue[UiEvent],
        config_path: Path,
        mock: bool = False,
        use_network: bool = True,
        dry_run: bool = False,
        download_only_default: bool = False,
    ) -> None:
        self.config = config
        self.controller = controller
        self.events = events
        self.config_path = Path(config_path)
        self.mock = mock
        self.use_network = use_network
        self.dry_run = dry_run
        self.results: dict[ComponentId, DetectionResult] = {}
        self.releases: dict[ComponentId, ReleaseInfo | None] = {}
        self.system: SystemInfo | None = None
        self.rows: dict[ComponentId, ComponentRow] = {}
        self.path_vars: dict[ComponentId, tk.StringVar] = {}
        self.path_entries: dict[ComponentId, ttk.Entry] = {}
        self.path_browse_buttons: dict[ComponentId, ttk.Button] = {}
        self._busy = False
        self._phase = ""
        self._pending_errors: list[str] = []
        self._log_expanded = True
        self.download_only_default = download_only_default
        self._auto_download_started = False

        self.root = tk.Tk()
        suffix = ""
        if dry_run:
            suffix += " [预览模式]"
        if mock:
            suffix += " [模拟数据]"
        self.root.title(constants.APP_TITLE + suffix)
        self.root.geometry("1120x800")
        self.root.minsize(1000, 700)
        self._configure_style()
        self._build_ui()
        if self.mock:
            self._append_log("模拟数据模式仅用于演示/测试，请勿据此执行真实安装", "WARNING")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(250, self._initial_detect)
        self.root.after(100, self._poll)

    # ---------- 样式 ----------
    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        base_font = ("Microsoft YaHei UI", 10)
        style.configure(".", font=base_font)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Section.TLabel", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("ComponentName.TLabel", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Hint.TLabel", foreground="#57606a", font=("Microsoft YaHei UI", 9))
        style.configure("Group.TLabel", foreground="#57606a", font=("Microsoft YaHei UI", 9))
        style.configure("StatusOk.TLabel", foreground="#1a7f37")
        style.configure("StatusUpdate.TLabel", foreground="#0969da")
        style.configure("StatusOutdated.TLabel", foreground="#bf6a00")
        style.configure("StatusMissing.TLabel", foreground="#cf222e")
        style.configure("StatusUnknown.TLabel", foreground="#6e7781")
        style.configure("TagRequired.TLabel", foreground="#cf222e", font=("Microsoft YaHei UI", 8))
        style.configure("TagRecommended.TLabel", foreground="#bf6a00", font=("Microsoft YaHei UI", 8))
        style.configure("TagOptional.TLabel", foreground="#6e7781", font=("Microsoft YaHei UI", 8))
        style.configure("OverallReady.TLabel", foreground="#1a7f37", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("OverallBasic.TLabel", foreground="#bf6a00", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("OverallBad.TLabel", foreground="#cf222e", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Footer.TLabel", foreground="#57606a", font=("Microsoft YaHei UI", 9))

    # ---------- 布局 ----------
    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=(16, 12, 16, 8))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=0)
        outer.rowconfigure(4, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=constants.APP_TITLE, style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.redetect_button = ttk.Button(
            header, text="重新检测", command=self._start_detect
        )
        self.redetect_button.grid(row=0, column=1, sticky="e")

        status_line = ttk.Frame(outer)
        status_line.grid(row=1, column=0, sticky="ew", pady=(6, 8))
        status_line.columnconfigure(1, weight=1)
        self.overall_label = ttk.Label(
            status_line, text="正在检测…", style="OverallBasic.TLabel"
        )
        self.overall_label.grid(row=0, column=0, sticky="w")
        self.system_label = ttk.Label(status_line, text="", style="Footer.TLabel")
        self.system_label.grid(row=0, column=1, sticky="w", padx=(16, 0))
        self.counts_label = ttk.Label(status_line, text="", style="Footer.TLabel")
        self.counts_label.grid(row=0, column=2, sticky="e")

        table = ttk.Frame(outer)
        table.grid(row=2, column=0, sticky="ew")
        table.columnconfigure(5, weight=1)
        self.table = table
        headers = ("选择", "组件", "状态", "已安装", "最新", "安装位置", "操作")
        for column, text in enumerate(headers):
            ttk.Label(
                table,
                text=text,
                style="Section.TLabel",
                anchor="w",
            ).grid(row=0, column=column, padx=4, pady=(0, 4), sticky="w")

        ttk.Label(table, text="── 目标应用 ──────────────────────────────", style="Group.TLabel").grid(
            row=1, column=0, columnspan=7, sticky="w", pady=(4, 0)
        )
        claude_row = ComponentRow(
            table,
            components.SPECS[ComponentId.CLAUDE],
            grid_row=2,
            on_action=self._on_row_action,
            on_toggle=self._update_button_count,
        )
        self.rows[ComponentId.CLAUDE] = claude_row

        ttk.Label(table, text="── 环境依赖 ──────────────────────────────", style="Group.TLabel").grid(
            row=3, column=0, columnspan=7, sticky="w", pady=(8, 0)
        )
        for offset, cid in enumerate(GROUP_ORDER_DEPENDENCIES, start=4):
            row = ComponentRow(
                table,
                components.SPECS[cid],
                grid_row=offset,
                on_action=self._on_row_action,
                on_toggle=self._update_button_count,
            )
            self.rows[cid] = row

        path_frame = ttk.LabelFrame(outer, text=" 路径设置 ", padding=(10, 8))
        path_frame.grid(row=3, column=0, sticky="ew", pady=(10, 8))
        path_frame.columnconfigure(1, weight=1)

        ttk.Label(path_frame, text="下载目录").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.download_var = tk.StringVar(value=self.config.download_dir)
        self.download_entry = ttk.Entry(path_frame, textvariable=self.download_var)
        self.download_entry.grid(row=0, column=1, sticky="ew")
        self.download_browse_button = ttk.Button(
            path_frame, text="浏览", command=self._browse_download_dir, width=7
        )
        self.download_browse_button.grid(row=0, column=2, padx=(6, 4))
        ttk.Button(
            path_frame, text="打开", command=self._open_download_dir, width=7
        ).grid(row=0, column=3)

        install_frame = ttk.Frame(path_frame)
        install_frame.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        install_frame.columnconfigure(1, weight=1)
        install_frame.columnconfigure(4, weight=1)
        for index, cid in enumerate(GROUP_ORDER_DEPENDENCIES):
            row = index // 2
            base = (index % 2) * 3
            ttk.Label(install_frame, text=components.SPECS[cid].display_name).grid(
                row=row, column=base, sticky="w", padx=(0 if base == 0 else 18, 6), pady=3
            )
            var = tk.StringVar(value=self.config.install_dirs.get(cid.value, ""))
            entry = ttk.Entry(install_frame, textvariable=var)
            entry.grid(row=row, column=base + 1, sticky="ew", pady=3)
            browse = ttk.Button(
                install_frame,
                text="浏览",
                width=6,
                command=lambda c=cid: self._browse_install_dir(c),
            )
            browse.grid(row=row, column=base + 2, sticky="e", padx=(6, 2), pady=3)
            entry.bind("<FocusOut>", lambda _event: self._save_config())
            entry.bind("<Return>", lambda _event: self._save_config())
            self.path_vars[cid] = var
            self.path_entries[cid] = entry
            self.path_browse_buttons[cid] = browse

        ttk.Label(
            path_frame,
            text="Claude Code：安装位置由 winget/npm/native 决定，安装后在此显示实际路径。"
            "  “最新”列带 * 表示远端不可用，使用内置版本。",
            style="Hint.TLabel",
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        advanced = ttk.Button(
            path_frame, text="高级设置…", command=self._open_advanced_settings
        )
        advanced.grid(row=2, column=3, sticky="e", pady=(6, 0))
        self.advanced_button = advanced

        progress_frame = ttk.LabelFrame(outer, text=" 进度与日志 ", padding=(10, 8))
        progress_frame.grid(row=4, column=0, sticky="nsew")
        progress_frame.columnconfigure(0, weight=1)
        progress_frame.rowconfigure(3, weight=1)

        task_line = ttk.Frame(progress_frame)
        task_line.grid(row=0, column=0, sticky="ew")
        task_line.columnconfigure(0, weight=1)
        self.task_label = ttk.Label(task_line, text="等待检测…")
        self.task_label.grid(row=0, column=0, sticky="w")
        self.progress_text = ttk.Label(task_line, text="", style="Footer.TLabel")
        self.progress_text.grid(row=0, column=1, sticky="e")

        self.progress = ttk.Progressbar(progress_frame, mode="determinate", maximum=100)
        self.progress.grid(row=1, column=0, sticky="ew", pady=(6, 4))

        log_bar = ttk.Frame(progress_frame)
        log_bar.grid(row=2, column=0, sticky="ew")
        log_bar.columnconfigure(0, weight=1)
        ttk.Label(log_bar, text="日志", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.log_toggle_button = ttk.Button(
            log_bar, text="收起", width=6, command=self._toggle_log
        )
        self.log_toggle_button.grid(row=0, column=1, sticky="e")

        self.log_frame = ttk.Frame(progress_frame)
        self.log_frame.grid(row=3, column=0, sticky="nsew", pady=(4, 0))
        self.log_frame.columnconfigure(0, weight=1)
        self.log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            self.log_frame,
            height=8,
            wrap="none",
            state="disabled",
            font=("Consolas", 9),
            background="#f6f8fa",
            relief="flat",
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self.log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.tag_configure("ERROR", foreground="#cf222e")
        self.log_text.tag_configure("WARNING", foreground="#bf6a00")

        bottom = ttk.Frame(outer)
        bottom.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        bottom.columnconfigure(0, weight=1)
        self.install_button = ttk.Button(
            bottom, text="安装选中项 (0)", command=lambda: self._start_install()
        )
        self.install_button.grid(row=0, column=0, sticky="w")
        self.download_only_button = ttk.Button(
            bottom, text="仅下载", command=lambda: self._start_install(download_only=True)
        )
        self.download_only_button.grid(row=0, column=1, padx=(8, 0))
        self.cancel_button = ttk.Button(
            bottom, text="取消", command=self._cancel, state=["disabled"]
        )
        self.cancel_button.grid(row=0, column=2, padx=(8, 0))
        ttk.Button(
            bottom, text="打开下载目录", command=self._open_download_dir
        ).grid(row=0, column=3, padx=(24, 0))
        ttk.Button(bottom, text="打开日志", command=self._open_log_dir).grid(
            row=0, column=4, padx=(8, 0)
        )

        footer = ttk.Frame(outer)
        footer.grid(row=6, column=0, sticky="ew", pady=(6, 0))
        admin_text = "管理员权限：是" if winreg_utils.is_admin() else "管理员权限：否"
        if not winreg_utils.is_admin():
            admin_text += "（安装 Node/Git 时会请求 UAC）"
        ttk.Label(footer, text=admin_text, style="Footer.TLabel").pack(side="left")
        ttk.Label(
            footer,
            text=f"配置：{self.config_path}",
            style="Footer.TLabel",
        ).pack(side="right")

    # ---------- 启动与事件轮询 ----------
    def run(self) -> None:
        self.root.mainloop()

    def _initial_detect(self) -> None:
        self._start_detect()

    def _poll(self) -> None:
        try:
            for _ in range(200):
                event = self.events.get_nowait()
                self._handle_event(event)
        except Empty:
            pass
        self.root.after(100, self._poll)

    def _handle_event(self, event: UiEvent) -> None:
        kind = event.kind
        data = event.data
        if kind == "system":
            self._on_system(data.get("info"))
        elif kind == "detection":
            self._on_detection(data.get("result"), data.get("release"))
        elif kind == "progress":
            self._on_progress(data)
        elif kind == "task_state":
            self._on_task_state(data)
        elif kind == "install_done":
            self._on_install_done(data.get("component"), data.get("result"))
        elif kind == "log":
            self._append_log(str(data.get("message", "")), str(data.get("level", "INFO")))
        elif kind == "error":
            self._pending_errors.append(str(data.get("message", "")))
            self._append_log(str(data.get("message", "")), "ERROR")
        elif kind == "done":
            self._on_done(data)

    def _on_system(self, info: SystemInfo | None) -> None:
        if info is None:
            return
        self.system = info
        ram = f" / {info.ram_gb} GB" if info.ram_gb else ""
        self.system_label.configure(
            text=f"{info.os_name} {info.os_version} / {info.arch.value}{ram}"
        )
        for warning in info.warnings:
            self._append_log(warning, "WARNING")

    def _on_detection(self, result: DetectionResult | None, release: ReleaseInfo | None) -> None:
        if result is None:
            return
        self.results[result.component] = result
        self.releases[result.component] = release
        row = self.rows.get(result.component)
        if row:
            row.update(result, release)
        self._sync_path_row(result)
        self._update_overall()
        self._update_button_count()

    def _sync_path_row(self, result: DetectionResult) -> None:
        cid = result.component
        if cid not in self.path_vars:
            return
        entry = self.path_entries[cid]
        browse = self.path_browse_buttons[cid]
        if result.status in (Status.MISSING, Status.UNKNOWN, Status.ERROR) or not result.location:
            current = self.path_vars[cid].get().strip()
            if not current:
                default_dir = str(config_module.default_install_dir(cid, self.config.python_version))
                self.path_vars[cid].set(self.config.install_dirs.get(cid.value) or default_dir)
            entry.state(["disabled"] if self._busy else ["!disabled", "!readonly"])
            browse.state(["disabled"] if self._busy else ["!disabled"])
        else:
            self.path_vars[cid].set(result.location)
            entry.state(["disabled"] if self._busy else ["readonly"])
            browse.state(["disabled"])

    def _update_overall(self) -> None:
        overall = OverallState.from_results(self.results, components.requirements())
        style = {
            "ready": "OverallReady.TLabel",
            "basic": "OverallBasic.TLabel",
            "not_ready": "OverallBad.TLabel",
        }.get(overall.level, "OverallBasic.TLabel")
        self.overall_label.configure(text=overall.headline, style=style)
        self.counts_label.configure(
            text=(
                f"必需 {overall.required_ok}/{overall.required_total}"
                f" | 推荐 {overall.recommended_ok}/{overall.recommended_total}"
                f" | 可选 {overall.optional_ok}/{overall.optional_total}"
            )
        )

    def _on_progress(self, data: dict) -> None:
        component = data.get("component")
        done = int(data.get("done") or 0)
        total = int(data.get("total") or 0)
        speed = float(data.get("speed") or 0)
        name = components.SPECS.get(component).display_name if component else "下载"
        if total:
            self.progress.stop()
            self.progress.configure(mode="determinate", value=min(100, done * 100 / total))
            self.progress_text.configure(
                text=f"{name} {_fmt_bytes(done)} / {_fmt_bytes(total)}  {_fmt_speed(speed)}"
            )
        else:
            self.progress_text.configure(text=f"{name} {_fmt_bytes(done)}  {_fmt_speed(speed)}")

    def _on_task_state(self, data: dict) -> None:
        text = str(data.get("text", ""))
        phase = str(data.get("phase", ""))
        busy = bool(data.get("busy", False))
        self.task_label.configure(text=text)
        self._phase = phase
        if busy:
            self._set_busy(True)
            if phase in ("installing", "checking", "checking_remote"):
                self.progress.configure(mode="indeterminate")
                self.progress.start(12)
                self.progress_text.configure(text="")
            else:
                self.progress.stop()
                self.progress.configure(mode="determinate", value=0)
            cancel_state = ["!disabled"] if phase == "downloading" else ["disabled"]
            self.cancel_button.state(cancel_state)
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
            self.progress_text.configure(text="")
            self.cancel_button.state(["disabled"])
            self._set_busy(False)

    def _on_install_done(self, component: ComponentId | None, result: InstallResult | None) -> None:
        if component is None or result is None:
            return
        name = components.SPECS[component].display_name
        if result.success:
            self._append_log(f"{name}: {result.message}", "INFO")
            if result.needs_reboot:
                self._append_log(f"{name}: 需要重启后完全生效", "WARNING")
        else:
            self._append_log(f"{name}: {result.message}", "ERROR")

    def _on_done(self, data: dict) -> None:
        kind = data.get("kind")
        cancelled = bool(data.get("cancelled"))
        failures = data.get("failures") or ()
        if cancelled:
            self._append_log("操作已取消", "WARNING")
        elif kind == "detect":
            self._append_log("检测完成", "INFO")
            if self.download_only_default and not self._auto_download_started:
                self._auto_download_started = True
                self.root.after(300, self._auto_download)
        elif kind == "install":
            if failures:
                self._pending_errors.append("以下组件未完成: " + "、".join(failures))
            else:
                self._append_log("安装流程完成", "INFO")
        self._update_overall()
        if self._pending_errors:
            message = "\n".join(dict.fromkeys(self._pending_errors))
            self._pending_errors.clear()
            messagebox.showwarning("未完成", message, parent=self.root)

    # ---------- 操作 ----------
    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = ["disabled"] if busy else ["!disabled"]
        for row in self.rows.values():
            row.set_busy(busy)
        self.redetect_button.state(state)
        self.advanced_button.state(state)
        self.download_entry.state(state)
        self.download_browse_button.state(state)
        for cid, entry in self.path_entries.items():
            if busy:
                entry.state(["disabled"])
                self.path_browse_buttons[cid].state(["disabled"])
            else:
                result = self.results.get(cid)
                if result and result.location and result.status not in (
                    Status.MISSING,
                    Status.UNKNOWN,
                    Status.ERROR,
                ):
                    entry.state(["readonly"])
                    self.path_browse_buttons[cid].state(["disabled"])
                else:
                    entry.state(["!disabled", "!readonly"])
                    self.path_browse_buttons[cid].state(["!disabled"])
        self._update_button_count()

    def _update_button_count(self) -> None:
        actionable = [
            cid
            for cid, row in self.rows.items()
            if row.is_selected()
            and row.result is not None
            and row.result.status in (Status.MISSING, Status.OUTDATED, Status.UPDATE_AVAILABLE)
        ]
        text = f"安装选中项 ({len(actionable)})"
        self.install_button.configure(text=text)
        if self._busy or not actionable:
            self.install_button.state(["disabled"])
            self.download_only_button.state(["disabled"])
        else:
            self.install_button.state(["!disabled"])
            download_ids = [cid for cid in actionable if cid != ComponentId.CLAUDE]
            self.download_only_button.state(["!disabled"] if download_ids else ["disabled"])

    def _start_detect(self) -> None:
        if self._busy:
            return
        self._save_config()
        self._pending_errors.clear()
        self.overall_label.configure(text="正在检测…", style="OverallBasic.TLabel")
        self.controller.start_detect(use_network=self.use_network, mock=self.mock)

    def _selected_actionable(self) -> list[ComponentId]:
        return [
            cid
            for cid, row in self.rows.items()
            if row.is_selected()
            and row.result is not None
            and row.result.status in (Status.MISSING, Status.OUTDATED, Status.UPDATE_AVAILABLE)
        ]

    def _auto_download(self) -> None:
        if self._busy:
            return
        actionable = [
            cid for cid in self._selected_actionable() if cid != ComponentId.CLAUDE
        ]
        if not actionable:
            self._append_log(
                "没有需要预下载的组件（Claude Code 由 winget/npm 在线安装）", "INFO"
            )
            return
        self._start_install(download_only=True)

    def _start_install(
        self, download_only: bool = False, only_cid: ComponentId | None = None
    ) -> None:
        if self._busy:
            return
        if only_cid is not None:
            ids = [only_cid]
        else:
            ids = self._selected_actionable()
        if not ids:
            show_error(self.root, "请先勾选需要安装或升级的组件")
            return
        if download_only:
            skipped = [cid for cid in ids if cid == ComponentId.CLAUDE]
            ids = [cid for cid in ids if cid != ComponentId.CLAUDE]
            if skipped:
                self._append_log("Claude Code 由 winget/npm 在线安装，不支持仅下载", "WARNING")
            if not ids:
                show_error(self.root, "当前选中项没有可预下载的安装包")
                return
        if not download_only:
            if not self._validate_paths(ids):
                return
            upgrades = [
                cid
                for cid in ids
                if self.results.get(cid)
                and self.results[cid].status in (Status.OUTDATED, Status.UPDATE_AVAILABLE)
            ]
            if upgrades:
                names = "、".join(components.SPECS[cid].display_name for cid in upgrades)
                if not ask_confirm(
                    self.root,
                    f"以下组件已安装，将静默覆盖升级到现有路径：\n\n{names}\n\n"
                    "建议先关闭正在使用它们的程序。是否继续？",
                    "升级确认",
                ):
                    return
                running = install.running_processes(["node.exe", "git.exe"])
                if running:
                    if not ask_confirm(
                        self.root,
                        "检测到以下进程正在运行：" + "、".join(running) +
                        "\n\n继续安装可能导致文件占用失败，是否仍要继续？",
                        "进程占用提醒",
                    ):
                        return
        self._save_config()
        if self.controller.start_install(ids, download_only=download_only, dry_run=self.dry_run):
            self._append_log(
                ("预览模式：" if self.dry_run else "")
                + ("开始仅下载: " if download_only else "开始安装: ")
                + "、".join(components.SPECS[cid].display_name for cid in ids),
                "INFO",
            )

    def _validate_paths(self, ids: list[ComponentId]) -> bool:
        for cid in ids:
            if cid not in self.path_vars:
                continue
            result = self.results.get(cid)
            if result and result.status in (Status.OUTDATED, Status.UPDATE_AVAILABLE) and result.location:
                continue
            value = self.path_vars[cid].get().strip()
            if not value:
                show_error(self.root, f"请填写 {components.SPECS[cid].display_name} 的安装目录")
                return False
            require_writable = True
            if cid in (ComponentId.NODE, ComponentId.GIT):
                # 系统级安装会提权，Program Files 默认可写。
                require_writable = False
            elif cid == ComponentId.PYTHON:
                lowered = value.lower()
                if lowered.startswith(str(constants.program_files()).lower()):
                    require_writable = False
                    self._append_log(
                        "Python 目标目录位于 Program Files，将按“所有用户”安装并请求管理员权限",
                        "WARNING",
                    )
            validation = config_module.validate_directory(
                value, cid, require_writable=require_writable
            )
            if not validation.ok:
                show_error(self.root, f"{components.SPECS[cid].display_name}: {validation.message}")
                return False
            if cid == ComponentId.VSCODE:
                lowered = value.lower()
                if lowered.startswith(str(constants.program_files()).lower()):
                    show_error(
                        self.root,
                        "VS Code 用户版不能安装到 Program Files，请选择用户可写目录。",
                    )
                    return False
            if validation.warning:
                if not ask_confirm(self.root, f"{components.SPECS[cid].display_name}: {validation.warning}\n是否继续？"):
                    return False
            self.config.install_dirs[cid.value] = value
        return True

    def _on_row_action(self, cid: ComponentId) -> None:
        result = self.results.get(cid)
        if result is None:
            return
        if result.status in (Status.OK, Status.UPDATE_AVAILABLE, Status.OUTDATED):
            if result.location and result.status == Status.OK:
                self._open_path(result.location)
                return
        if result.status in (Status.MISSING, Status.OUTDATED, Status.UPDATE_AVAILABLE):
            self._start_install(only_cid=cid)
            return
        if result.location:
            self._open_path(result.location)
        else:
            self._start_detect()

    def _cancel(self) -> None:
        self.controller.cancel()
        self._append_log("已请求取消当前下载", "WARNING")
        self.cancel_button.state(["disabled"])

    # ---------- 路径与配置 ----------
    def _browse_download_dir(self) -> None:
        path = filedialog.askdirectory(
            parent=self.root,
            title="选择下载目录",
            initialdir=self.download_var.get() or str(constants.default_download_dir()),
        )
        if path:
            self.download_var.set(path)
            self._save_config()

    def _browse_install_dir(self, cid: ComponentId) -> None:
        var = self.path_vars[cid]
        path = filedialog.askdirectory(
            parent=self.root,
            title=f"选择 {components.SPECS[cid].display_name} 安装目录",
            initialdir=var.get() or str(config_module.default_install_dir(cid, self.config.python_version)),
        )
        if path:
            var.set(path)
            self._save_config()

    def _save_config(self) -> None:
        download = self.download_var.get().strip()
        if download:
            self.config.download_dir = download
        for cid, var in self.path_vars.items():
            result = self.results.get(cid)
            if result is not None and result.status in (Status.OK,):
                continue
            value = var.get().strip()
            if value:
                self.config.install_dirs[cid.value] = value
        try:
            self.config.save(self.config_path)
        except OSError as exc:
            self._append_log(f"保存配置失败: {exc}", "ERROR")

    def _open_advanced_settings(self) -> None:
        if self._busy:
            return
        dialog = AdvancedSettingsDialog(self.root, self.config)
        self.root.wait_window(dialog)
        if dialog.saved:
            self._save_config()
            self._append_log("高级设置已更新，重新检测以刷新最新版本", "INFO")

    # ---------- 打开目录与日志 ----------
    def _open_path(self, path: str) -> None:
        target = Path(path)
        if not target.exists():
            show_error(self.root, f"路径不存在：{path}")
            return
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
        except OSError as exc:
            show_error(self.root, f"无法打开路径：{exc}")

    def _open_download_dir(self) -> None:
        path = Path(self.download_var.get().strip() or self.config.download_dir)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            show_error(self.root, f"无法创建下载目录：{exc}")
            return
        self._open_path(str(path))

    def _open_log_dir(self) -> None:
        path = constants.log_dir()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            show_error(self.root, f"无法创建日志目录：{exc}")
            return
        self._open_path(str(path))

    def _toggle_log(self) -> None:
        if self._log_expanded:
            self.log_frame.grid_remove()
            self.log_toggle_button.configure(text="展开")
        else:
            self.log_frame.grid()
            self.log_toggle_button.configure(text="收起")
        self._log_expanded = not self._log_expanded

    def _append_log(self, message: str, level: str = "INFO") -> None:
        if not message:
            return
        self.log_text.configure(state="normal")
        tag = level if level in ("ERROR", "WARNING") else ""
        self.log_text.insert("end", message + "\n", tag)
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 1000:
            self.log_text.delete("1.0", f"{lines - 1000}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_close(self) -> None:
        if self._busy:
            if not ask_confirm(
                self.root,
                "当前有检测/下载/安装任务正在进行。\n"
                "关闭窗口会取消下载；若安装器已启动，它可能继续运行。\n\n确定退出？",
                "退出确认",
            ):
                return
            self.controller.cancel()
        self._save_config()
        self.root.destroy()
