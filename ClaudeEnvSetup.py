"""PyInstaller 入口：保证 app 包使用正常的相对导入。"""

from __future__ import annotations

from app.main import main

if __name__ == "__main__":
    raise SystemExit(main())
