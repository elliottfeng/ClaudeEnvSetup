"""高级设置与通用对话框。"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ..config import AppConfig


class AdvancedSettingsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, config: AppConfig) -> None:
        super().__init__(parent)
        self.title("高级设置")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.config_ref = config
        self.saved = False

        self.source_var = tk.StringVar(value=config.network.mode)
        self.proxy_var = tk.StringVar(value=config.network.proxy or "")
        self.python_all_var = tk.BooleanVar(value=config.install_options.python_all_users)

        body = ttk.Frame(self, padding=16)
        body.grid(row=0, column=0, sticky="nsew")

        ttk.Label(body, text="下载源", style="Section.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 4)
        )
        source_frame = ttk.Frame(body)
        source_frame.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 12))
        for index, (value, text) in enumerate(
            (
                ("auto", "自动（官方优先，失败切镜像）"),
                ("official", "仅官方源"),
                ("mirror", "仅国内镜像"),
            )
        ):
            ttk.Radiobutton(
                source_frame, text=text, value=value, variable=self.source_var
            ).grid(row=index, column=0, sticky="w", pady=2)

        ttk.Label(body, text="代理（可选）", style="Section.TLabel").grid(
            row=2, column=0, sticky="w", pady=(0, 4)
        )
        self.proxy_entry = ttk.Entry(body, textvariable=self.proxy_var, width=44)
        self.proxy_entry.grid(row=3, column=0, columnspan=2, sticky="we", pady=(0, 4))
        ttk.Label(
            body,
            text="留空使用系统代理，例如 http://127.0.0.1:7890",
            style="Hint.TLabel",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 12))

        ttk.Separator(body, orient="horizontal").grid(
            row=5, column=0, columnspan=2, sticky="we", pady=(0, 10)
        )
        ttk.Checkbutton(
            body,
            text="Python 为所有用户安装（需要管理员权限）",
            variable=self.python_all_var,
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(0, 12))

        ttk.Label(
            body,
            text="说明：Git/Node.js 的系统级安装需要管理员权限；\n"
            "Python/VS Code 默认按当前用户安装，不会弹 UAC。\n"
            "Claude Code 的位置由 winget/npm/native 安装方式决定。",
            style="Hint.TLabel",
            justify="left",
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(0, 14))

        buttons = ttk.Frame(body)
        buttons.grid(row=8, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="恢复默认", command=self._reset).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(buttons, text="取消", command=self.destroy).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(buttons, text="确定", command=self._ok).pack(side="left")

        self.bind("<Escape>", lambda _event: self.destroy())
        self.update_idletasks()
        self._center(parent)

    def _center(self, parent: tk.Misc) -> None:
        try:
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            w = self.winfo_width()
            h = self.winfo_height()
            self.geometry(f"+{px + max(0, (pw - w) // 2)}+{py + max(0, (ph - h) // 3)}")
        except tk.TclError:
            pass

    def _reset(self) -> None:
        self.source_var.set("auto")
        self.proxy_var.set("")
        self.python_all_var.set(False)

    def _ok(self) -> None:
        self.config_ref.network.mode = self.source_var.get()
        self.config_ref.network.proxy = self.proxy_var.get().strip() or None
        self.config_ref.install_options.python_all_users = bool(self.python_all_var.get())
        self.saved = True
        self.destroy()


def show_error(parent: tk.Misc, message: str, title: str = "错误") -> None:
    messagebox.showerror(title, message, parent=parent)


def ask_confirm(parent: tk.Misc, message: str, title: str = "确认") -> bool:
    return bool(messagebox.askyesno(title, message, parent=parent))
