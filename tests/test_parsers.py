from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.detect import (
    _git_root_from_cli,
    parse_claude_version,
    parse_code_version,
    parse_git_version,
    parse_node_version,
    parse_py_launcher_listing,
)


class ParserTests(unittest.TestCase):
    def test_git(self) -> None:
        self.assertEqual(parse_git_version("git version 2.56.0.windows.3"), (2, 56, 0))

    def test_node(self) -> None:
        self.assertEqual(parse_node_version("v24.22.1"), (24, 22, 1))

    def test_claude(self) -> None:
        self.assertEqual(parse_claude_version("2.1.319 (Claude Code)"), (2, 1, 319))

    def test_code(self) -> None:
        self.assertEqual(
            parse_code_version("1.145.3\ndcd3ad6e368fbe19d03f37054a40dbaf40d676f4\nx64"),
            (1, 145, 3),
        )

    def test_py_launcher_listing(self) -> None:
        text = (
            " -V:3.15 *        C:\\Python315\\python.exe\n"
            " -V:3.10          D:\\Tools\\Python 3.10\\python.exe\n"
        )
        result = parse_py_launcher_listing(text)
        self.assertEqual(result[0], ((3, 15), r"C:\Python315\python.exe"))
        self.assertEqual(result[1], ((3, 10), r"D:\Tools\Python 3.10\python.exe"))

    def test_git_root_from_cli(self) -> None:
        with tempfile.TemporaryDirectory(prefix="claudeenv-git-") as temp:
            root = Path(temp)
            (root / "cmd").mkdir()
            (root / "cmd" / "git.exe").write_bytes(b"stub")
            exe = root / "ucrt64" / "bin" / "git.exe"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"stub")
            self.assertEqual(Path(_git_root_from_cli(exe)), root)


if __name__ == "__main__":
    unittest.main()
