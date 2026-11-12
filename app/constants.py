"""应用常量、默认路径、超时与固定版本。"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "ClaudeEnvSetup"
APP_TITLE = "Claude Code 环境配置助手"
APP_VERSION = "1.0.0"

# 网络超时（秒）
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 20
CLI_TIMEOUT = 10
DOWNLOAD_READ_TIMEOUT = 30
DOWNLOAD_RETRIES = 3
HTTP_RETRIES = 2

# 内置固定版本（远端不可用时兜底，2026-11-12 核实）
PINNED_NODE = "26.13.1"
PINNED_GIT = "2.56.1.windows.1"
PINNED_PYTHON = "3.15.2"
PINNED_VSCODE = "1.146.0"
PINNED_CLAUDE = "2.1.318"

# 官方元数据
NODE_INDEX_URL = "https://nodejs.org/dist/index.json"
NODE_DIST_BASE = "https://nodejs.org/dist"
NODE_DIST_MIRROR = "https://registry.npmmirror.com/-/binary/node"

GIT_LATEST_API = "https://api.github.com/repos/git-for-windows/git/releases/latest"
GIT_MIRROR_BASE = "https://registry.npmmirror.com/-/binary/git-for-windows"

PYTHON_RELEASE_API = (
    "https://www.python.org/api/v2/downloads/release/?is_published=true"
)
PYTHON_FTP_LIST = "https://www.python.org/ftp/python/"
PYTHON_FTP_BASE = "https://www.python.org/ftp/python"
PYTHON_MIRROR_BASE = "https://registry.npmmirror.com/-/binary/python"

VSCODE_UPDATE_API = (
    "https://update.code.visualstudio.com/api/update/win32-{arch}-user/stable/latest"
)
VSCODE_PINNED_URL = (
    "https://update.code.visualstudio.com/{version}/win32-{arch}-user/stable"
)

NPM_CLAUDE_LATEST = "https://registry.npmjs.org/@anthropic-ai/claude-code/latest"
NPM_MIRROR_CLAUDE_LATEST = (
    "https://registry.npmmirror.com/@anthropic-ai/claude-code/latest"
)
NPM_MIRROR_REGISTRY = "https://registry.npmmirror.com"

CLAUDE_DOC_URL = "https://code.claude.com/docs/en/setup"
CLAUDE_INSTALL_PS1 = "https://claude.ai/install.ps1"

WINGET_CLAUDE_ID = "Anthropic.ClaudeCode"
WINGET_DOWNLOAD_SOURCE = "winget"

USER_AGENT = f"{APP_NAME}/{APP_VERSION} (Windows)"


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_NAME


def local_app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_NAME


def log_dir() -> Path:
    return local_app_data_dir() / "logs"


def cache_dir() -> Path:
    return local_app_data_dir() / "cache"


def default_download_dir() -> Path:
    return Path.home() / "Downloads" / APP_NAME


def config_path() -> Path:
    return app_data_dir() / "config.json"


def program_files() -> Path:
    return Path(os.environ.get("ProgramFiles", r"C:\Program Files"))


def local_programs() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Programs"
