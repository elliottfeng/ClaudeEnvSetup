from __future__ import annotations

import unittest

from app.versioning import (
    compare_versions,
    format_version,
    is_newer,
    is_outdated,
    is_prerelease,
    parse_version,
)


class VersioningTests(unittest.TestCase):
    def test_parse_common_outputs(self) -> None:
        cases = {
            "v24.22.1": (24, 22, 1),
            "git version 2.56.0.windows.3": (2, 56, 0),
            "2.56.0.3": (2, 56, 0, 3),
            "2.1.319 (Claude Code)": (2, 1, 319),
            "Python 3.15.1": (3, 15, 1),
            "1.146.0-insider": (1, 146, 0),
            "3.15.2rc1": (3, 15, 2),
            "": None,
            "not a version": None,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_version(text), expected)

    def test_compare_pads_short_versions(self) -> None:
        self.assertEqual(compare_versions((2, 56), (2, 56, 0)), 0)
        self.assertEqual(compare_versions((2, 56), (2, 56, 1)), -1)
        self.assertEqual(compare_versions((3, 15, 2), (3, 15)), 1)

    def test_newer_and_outdated(self) -> None:
        self.assertTrue(is_newer((3, 15, 1), (3, 15, 2)))
        self.assertFalse(is_newer((3, 15, 2), (3, 15, 2)))
        self.assertTrue(is_outdated((20, 11, 0), (22, 0, 0)))
        self.assertFalse(is_outdated((24, 22, 1), (22, 0, 0)))
        self.assertFalse(is_outdated(None, None))

    def test_prerelease(self) -> None:
        for text in ("3.16.0a1", "3.15.2rc1", "1.146.0-insider", "2.0.0-beta.1"):
            with self.subTest(text=text):
                self.assertTrue(is_prerelease(text))
        for text in ("3.15.2", "2.1.319 (Claude Code)", "v24.22.1"):
            with self.subTest(text=text):
                self.assertFalse(is_prerelease(text))

    def test_format(self) -> None:
        self.assertEqual(format_version((3, 15, 2)), "3.15.2")
        self.assertEqual(format_version(None), "")
        self.assertEqual(format_version("custom"), "custom")


if __name__ == "__main__":
    unittest.main()
