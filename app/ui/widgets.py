"""组件行与状态标签。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from ..components import ComponentSpec
from ..models import ComponentId, DetectionResult, ReleaseInfo, Requirement, Status

STATUS_TEXT = {
    Status.OK: "✓ 已安装",
    Status.UPDATE_AVAILABLE: "↑ 可升级",
    Status.OUTDATED: "! 版本过低",
    Status.MISSING: "✗ 未安装",
    Status.UNKNOWN: "? 版本未知",
    Status.ERROR: "! 检测失败",
}

STATUS_STYLE = {
    Status.OK: "StatusOk.TLabel",
    Status.UPDATE_AVAILABLE: "StatusUpdate.TLabel",
    Status.OUTDATED: "StatusOutdated.TLabel",
    Status.MISSING: "StatusMissing.TLabel",
    Status.UNKNOWN: "StatusUnknown.TLabel",
    Status.ERROR: "StatusUnknown.TLabel",
}

REQUIREMENT_TEXT = {
    Requirement.REQUIRED: "必需",
    Requirement.RECOMMENDED: "推荐",
    Requirement.OPTIONAL: "可选",
}

REQUIREMENT_STYLE = {
    Requirement.REQUIRED: "TagRequired.TLabel",
    Requirement.RECOMMENDED: "TagRecommended.TLabel",
    Requirement.OPTIONAL: "TagOptional.TLabel",
}


def shorten(text: str, limit: int = 52) -> str:
    if not text:
        return "-"
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


class ComponentRow:
    def __init__(
        self,
        parent: ttk.Frame,
        spec: ComponentSpec,
        grid_row: int,
        on_action: Callable[[ComponentId], None],
        on_toggle: Callable[[], None],
    ) -> None:
        self.spec = spec
        self.on_action = on_action
        self.selected = tk.BooleanVar(value=False)
        self.result: DetectionResult | None = None
        self.release: ReleaseInfo | None = None
        self._initialized = False
        self._previous_status: Status | None = None
        self._busy = False

        self.check = ttk.Checkbutton(
            parent,
            variable=self.selected,
            command=on_toggle,
            width=3,
        )
        self.check.grid(row=grid_row, column=0, padx=(6, 2), pady=4, sticky="w")

        name_frame = ttk.Frame(parent)
        name_frame.grid(row=grid_row, column=1, padx=4, pady=4, sticky="w")
        ttk.Label(name_frame, text=spec.display_name, style="ComponentName.TLabel").pack(
            side="left"
        )
        ttk.Label(
            name_frame,
            text=REQUIREMENT_TEXT[spec.requirement],
            style=REQUIREMENT_STYLE[spec.requirement],
        ).pack(side="left", padx=(6, 0))

        self.status_label = ttk.Label(parent, text="-", style="StatusUnknown.TLabel", width=12)
        self.status_label.grid(row=grid_row, column=2, padx=4, pady=4, sticky="w")

        self.installed_label = ttk.Label(parent, text="-", width=11, anchor="w")
        self.installed_label.grid(row=grid_row, column=3, padx=4, pady=4, sticky="w")

        self.latest_label = ttk.Label(parent, text="-", width=13, anchor="w")
        self.latest_label.grid(row=grid_row, column=4, padx=4, pady=4, sticky="w")

        self.location_label = ttk.Label(parent, text="-", anchor="w")
        self.location_label.grid(row=grid_row, column=5, padx=4, pady=4, sticky="ew")

        self.action_button = ttk.Button(
            parent, text="安装", width=9, command=self._handle_action
        )
        self.action_button.grid(row=grid_row, column=6, padx=(6, 8), pady=4, sticky="e")

    @property
    def component_id(self) -> ComponentId:
        return self.spec.id

    def _handle_action(self) -> None:
        self.on_action(self.spec.id)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = ["disabled"] if busy else ["!disabled"]
        self.check.state(state)
        self.action_button.state(state)

    def update(self, result: DetectionResult, release: ReleaseInfo | None) -> None:
        previous = self._previous_status
        self.result = result
        self.release = release
        self.status_label.configure(
            text=STATUS_TEXT.get(result.status, "?"),
            style=STATUS_STYLE.get(result.status, "StatusUnknown.TLabel"),
        )
        self.installed_label.configure(text=result.installed_version or "-")
        if release is None:
            latest_text = "-"
        else:
            latest_text = release.version + (" *" if release.is_pinned else "")
        self.latest_label.configure(text=latest_text)
        if result.location:
            location_text = shorten(result.location)
        elif result.component == ComponentId.CLAUDE:
            location_text = "安装后显示"
        else:
            location_text = "-"
        self.location_label.configure(text=location_text)

        if result.status == Status.MISSING:
            action = "安装"
        elif result.status in (Status.OUTDATED, Status.UPDATE_AVAILABLE):
            action = "升级"
        elif result.status == Status.OK:
            action = "打开目录" if result.location else "重新检测"
        else:
            action = "打开目录" if result.location else "重新检测"
        self.action_button.configure(text=action)

        if not self._initialized:
            self.selected.set(result.status in (Status.MISSING, Status.OUTDATED))
            self._initialized = True
        elif result.status == Status.OK and previous in (
            Status.MISSING,
            Status.OUTDATED,
            Status.UPDATE_AVAILABLE,
        ):
            self.selected.set(False)
        self._previous_status = result.status

    def set_selected(self, value: bool) -> None:
        self.selected.set(value)

    def is_selected(self) -> bool:
        return bool(self.selected.get())
