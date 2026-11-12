from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.config import AppConfig, default_install_dir, validate_directory
from app.models import ComponentId


class ConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        config = AppConfig()
        self.assertTrue(config.download_dir)
        for cid in (ComponentId.GIT, ComponentId.NODE, ComponentId.PYTHON, ComponentId.VSCODE):
            self.assertIn(cid.value, config.install_dirs)
            self.assertEqual(config.install_dirs[cid.value], "")
        self.assertEqual(config.network.mode, "auto")

    def test_save_and_load(self) -> None:
        with tempfile.TemporaryDirectory(prefix="claudeenv-config-") as temp:
            path = Path(temp) / "config.json"
            config = AppConfig()
            config.download_dir = r"D:\下载 目录"
            config.install_dirs[ComponentId.NODE.value] = r"D:\Node.js"
            config.network.mode = "mirror"
            config.install_options.python_all_users = True
            config.save(path)
            loaded = AppConfig.load(path)
            self.assertEqual(loaded.download_dir, r"D:\下载 目录")
            self.assertEqual(loaded.install_dirs[ComponentId.NODE.value], r"D:\Node.js")
            self.assertEqual(loaded.network.mode, "mirror")
            self.assertTrue(loaded.install_options.python_all_users)

    def test_corrupt_json_backup(self) -> None:
        with tempfile.TemporaryDirectory(prefix="claudeenv-config-") as temp:
            path = Path(temp) / "config.json"
            path.write_text("{not json", encoding="utf-8")
            config = AppConfig.load(path)
            self.assertTrue(config.download_dir)
            self.assertTrue((Path(temp) / "config.json.bad").exists())

    def test_unknown_fields_and_bad_sections(self) -> None:
        with tempfile.TemporaryDirectory(prefix="claudeenv-config-") as temp:
            path = Path(temp) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "download_dir": "",
                        "unknown": 1,
                        "install_options": "bad",
                        "network": {"mode": "invalid"},
                    }
                ),
                encoding="utf-8",
            )
            config = AppConfig.load(path)
            self.assertEqual(config.network.mode, "auto")
            self.assertIsNotNone(config.install_options)

    def test_default_install_dir(self) -> None:
        self.assertEqual(default_install_dir(ComponentId.PYTHON, "3.15").name, "Python315")
        self.assertEqual(default_install_dir(ComponentId.PYTHON, "3.13.9").name, "Python313")
        self.assertEqual(default_install_dir(ComponentId.GIT).name, "Git")
        self.assertEqual(default_install_dir(ComponentId.NODE).name, "nodejs")

    def test_validate_directory(self) -> None:
        self.assertFalse(validate_directory("relative\\path").ok)
        self.assertFalse(validate_directory("C:\\").ok)
        self.assertFalse(validate_directory("C:\\bad|name").ok)
        with tempfile.TemporaryDirectory(prefix="claudeenv-path-") as temp:
            self.assertTrue(validate_directory(Path(temp) / "child").ok)
            file_path = Path(temp) / "file.txt"
            file_path.write_text("x", encoding="utf-8")
            self.assertFalse(validate_directory(file_path).ok)

    def test_require_writable_false(self) -> None:
        result = validate_directory(
            r"C:\Program Files\CustomNode",
            ComponentId.NODE,
            require_writable=False,
        )
        self.assertTrue(result.ok)

    def test_install_dir_for(self) -> None:
        config = AppConfig()
        detected = r"D:\Detected\Node"
        self.assertEqual(
            config.install_dir_for(ComponentId.NODE, detected),
            detected,
        )
        config.install_dirs[ComponentId.NODE.value] = r"D:\Custom\Node"
        self.assertEqual(
            config.install_dir_for(ComponentId.NODE, detected),
            r"D:\Custom\Node",
        )


if __name__ == "__main__":
    unittest.main()
