"""检测/下载/安装流程编排：单工作线程 + 事件队列。"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from pathlib import Path
from queue import Queue
from typing import Iterable

from . import components, constants, detect, install, remote
from .config import AppConfig
from .download import DownloadCancelled, DownloadError, DownloadManager
from .logging_setup import get_logger
from .models import (
    ComponentId,
    DetectionResult,
    InstallResult,
    ReleaseInfo,
    Status,
    SystemInfo,
    UiEvent,
)


class Controller:
    def __init__(self, config: AppConfig, events: Queue[UiEvent]) -> None:
        self.config = config
        self.events = events
        self.log = get_logger()
        self.cancel_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._detections: dict[ComponentId, DetectionResult] = {}
        self._releases: dict[ComponentId, ReleaseInfo | None] = {}
        self._system: SystemInfo | None = None
        self._arch = detect.get_system_info().arch
        self._cache = remote.ReleaseCache()
        self._busy = False
        self._mock = False

    # ---------- 公共接口 ----------
    @property
    def busy(self) -> bool:
        return self._busy

    def detections(self) -> dict[ComponentId, DetectionResult]:
        with self._lock:
            return dict(self._detections)

    def releases(self) -> dict[ComponentId, ReleaseInfo | None]:
        with self._lock:
            return dict(self._releases)

    def system(self) -> SystemInfo | None:
        return self._system

    def start_detect(self, use_network: bool = True, mock: bool = False) -> bool:
        if self._busy:
            return False
        self._busy = True
        self._mock = mock
        self.cancel_event.clear()
        self._thread = threading.Thread(
            target=self._detect_worker, args=(use_network, mock), daemon=True
        )
        self._thread.start()
        return True

    def start_install(
        self,
        ids: Iterable[ComponentId],
        download_only: bool = False,
        dry_run: bool = False,
    ) -> bool:
        if self._busy:
            return False
        selected = set(ids)
        if not selected:
            return False
        self._busy = True
        self.cancel_event.clear()
        self._thread = threading.Thread(
            target=self._install_worker,
            args=(selected, download_only, dry_run, self._mock),
            daemon=True,
        )
        self._thread.start()
        return True

    def cancel(self) -> None:
        self.cancel_event.set()

    # ---------- 事件 ----------
    def _emit(self, event_kind: str, **data) -> None:
        self.events.put(UiEvent(event_kind, data))

    def _task(self, text: str, phase: str = "", busy: bool = True) -> None:
        self._emit("task_state", text=text, phase=phase, busy=busy)

    # ---------- 检测 ----------
    def _detect_worker(self, use_network: bool, mock: bool) -> None:
        try:
            self._task("正在检测本地环境…", "checking")
            system = detect.get_system_info()
            self._system = system
            self._emit("system", info=system)
            local = detect.detect_all(mock=mock)
            with self._lock:
                self._detections = local
            for cid in components.DISPLAY_ORDER:
                self._emit("detection", result=local[cid], release=None)
            self._task("正在获取最新版本信息…", "checking_remote")
            releases = remote.resolve_all(
                self._arch, self.config, use_network=use_network, cache=self._cache
            )
            with self._lock:
                self._releases = releases
            enriched: dict[ComponentId, DetectionResult] = {}
            for cid in components.DISPLAY_ORDER:
                result = components.apply_release(local[cid], releases.get(cid))
                enriched[cid] = result
                self._emit("detection", result=result, release=releases.get(cid))
            with self._lock:
                self._detections = enriched
            self._emit("done", kind="detect", cancelled=False)
        except Exception as exc:  # pragma: no cover - 防御性
            self.log.exception("检测流程失败")
            self._emit("error", message=f"检测失败: {exc}")
            self._emit("done", kind="detect", cancelled=False)
        finally:
            self._busy = False
            self._task("检测完成", "ready", busy=False)

    # ---------- 安装 ----------
    def _ensure_release(self, cid: ComponentId, use_network: bool) -> ReleaseInfo | None:
        with self._lock:
            release = self._releases.get(cid)
        if release is not None:
            return release
        release = remote.resolve_component(
            cid, self._arch, self.config, use_network=use_network, cache=self._cache
        )
        with self._lock:
            self._releases[cid] = release
        return release

    def _progress(self, component: ComponentId, done: int, total: int, speed: float) -> None:
        self._emit(
            "progress",
            component=component,
            done=done,
            total=total,
            speed=speed,
        )

    def _install_worker(
        self,
        selected: set[ComponentId],
        download_only: bool,
        dry_run: bool,
        mock: bool,
    ) -> None:
        failures: list[str] = []
        try:
            self._task("准备下载/安装…", "installing")
            manager = DownloadManager(
                proxy=self.config.network.proxy,
                retries=max(1, self.config.network.retries),
            )
            for cid in components.INSTALL_ORDER:
                if cid not in selected:
                    continue
                if self.cancel_event.is_set():
                    self._emit("done", kind="install", cancelled=True)
                    return
                display = components.SPECS[cid].display_name
                release = self._ensure_release(cid, use_network=not mock)
                if release is None:
                    failures.append(display)
                    self._emit(
                        "install_done",
                        component=cid,
                        result=InstallResult(False, None, "无法获取最新版本信息"),
                    )
                    continue
                downloaded: Path | None = None
                if release.filename and release.urls:
                    if dry_run:
                        downloaded = Path(self.config.download_dir) / release.filename
                        self.log.info(
                            "预览下载: %s -> %s", release.filename, downloaded
                        )
                    else:
                        self._task(
                            f"下载 {display} {release.version}…", "downloading"
                        )
                        try:
                            downloaded = manager.fetch(
                                release,
                                self.config.download_dir,
                                progress=self._progress,
                                cancel=self.cancel_event,
                            )
                        except DownloadCancelled:
                            self._emit("done", kind="install", cancelled=True)
                            return
                        except DownloadError as exc:
                            failures.append(display)
                            self._emit(
                                "install_done",
                                component=cid,
                                result=InstallResult(False, None, str(exc)),
                            )
                            continue
                elif download_only:
                    self.log.info("%s 不支持仅下载，已跳过", display)
                    continue

                if download_only:
                    self.log.info("已下载 %s %s", display, release.version)
                    self._emit(
                        "install_done",
                        component=cid,
                        result=InstallResult(True, 0, "仅下载完成"),
                    )
                    continue

                detection = self.detections().get(cid)
                install_dir = components.install_dir_for(cid, self.config, detection)
                all_users = (
                    components.python_all_users(self.config, install_dir, detection)
                    if cid == ComponentId.PYTHON
                    else None
                )
                log_path = constants.log_dir() / (
                    f"install-{cid.value}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
                )
                prefer_npm = bool(
                    cid == ComponentId.CLAUDE
                    and detection is not None
                    and detection.status == Status.UPDATE_AVAILABLE
                )
                try:
                    plan = components.build_install_plan(
                        cid,
                        release,
                        self.config,
                        downloaded,
                        install_dir,
                        str(log_path),
                        python_all_users=all_users,
                        prefer_npm=prefer_npm,
                    )
                except (ValueError, OSError) as exc:
                    failures.append(display)
                    self._emit(
                        "install_done",
                        component=cid,
                        result=InstallResult(False, None, str(exc)),
                    )
                    continue
                self._task(f"安装 {display} {release.version}…", "installing")
                result = install.run_plan(plan, self.cancel_event, dry_run, self.log)
                self._emit("install_done", component=cid, result=result)
                if not result.success:
                    failures.append(display)
                    if result.cancelled:
                        self._emit("done", kind="install", cancelled=True)
                        return
                    continue
                if dry_run:
                    continue
                time.sleep(2)
                extra: list[str] = []
                if install_dir:
                    extra.extend([str(Path(install_dir) / "bin"), str(Path(install_dir) / "cmd")])
                if cid == ComponentId.CLAUDE:
                    appdata = os.environ.get("APPDATA")
                    if appdata:
                        extra.append(str(Path(appdata) / "npm"))
                install.refresh_environment(extra)
                try:
                    refreshed = detect.DETECTORS[cid]()
                except Exception as exc:
                    refreshed = DetectionResult(
                        component=cid, status=Status.ERROR, error=str(exc)
                    )
                refreshed = components.apply_release(refreshed, release)
                with self._lock:
                    self._detections[cid] = refreshed
                self._emit("detection", result=refreshed, release=release)
                if result.needs_reboot:
                    self._emit(
                        "log",
                        level="WARNING",
                        message=f"{display} 安装完成，建议重启后再使用新版本",
                    )
            if failures:
                self._emit("error", message="以下组件未完成: " + "、".join(failures))
            self._emit("done", kind="install", cancelled=False, failures=tuple(failures))
        except Exception as exc:  # pragma: no cover - 防御性
            self.log.exception("安装流程异常")
            self._emit("error", message=f"安装流程异常: {exc}")
            self._emit("done", kind="install", cancelled=False)
        finally:
            self._busy = False
            self._task("操作完成", "ready", busy=False)
