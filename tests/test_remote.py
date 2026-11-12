from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.models import Arch, ComponentId
from app.remote import (
    ReleaseCache,
    _sources,
    parse_claude_npm,
    parse_git_mirror_listing,
    parse_git_release,
    parse_node_index,
    parse_python_mirror_listing,
    parse_python_releases,
    parse_vscode_update,
    pinned_release,
)


class RemoteParserTests(unittest.TestCase):
    def test_node_lts_and_arch(self) -> None:
        data = [
            {"version": "v27.0.0", "lts": False, "files": ["win-x64-msi", "win-arm64-msi"]},
            {"version": "v26.13.1", "lts": "Lithium", "files": ["win-x64-msi", "win-arm64-msi"]},
            {"version": "v22.20.0", "lts": "Jod", "files": ["win-x64-msi"]},
        ]
        release = parse_node_index(data, Arch.X64)
        self.assertIsNotNone(release)
        assert release is not None
        self.assertEqual(release.component, ComponentId.NODE)
        self.assertEqual(release.version, "26.13.1")
        self.assertEqual(release.filename, "node-v26.13.1-x64.msi")
        self.assertEqual([url.source for url in release.urls], ["official", "mirror"])

        arm = parse_node_index(data, Arch.ARM64)
        assert arm is not None
        self.assertEqual(arm.filename, "node-v26.13.1-arm64.msi")

    def test_node_skips_versions_without_installer(self) -> None:
        data = [{"version": "v26.13.1", "lts": "Lithium", "files": ["linux-x64"]}]
        self.assertIsNone(parse_node_index(data, Arch.X64))

    def test_git_release(self) -> None:
        data = {
            "tag_name": "v2.56.1.windows.1",
            "prerelease": False,
            "assets": [
                {
                    "name": "Git-2.56.1-64-bit.exe",
                    "browser_download_url": "https://github.com/example/Git-2.56.1-64-bit.exe",
                }
            ],
        }
        release = parse_git_release(data, Arch.X64)
        assert release is not None
        self.assertEqual(release.version, "2.56.1")
        self.assertEqual(release.filename, "Git-2.56.1-64-bit.exe")
        self.assertEqual(release.note, "v2.56.1.windows.1")

    def test_git_mirror_listing(self) -> None:
        data = [
            {"name": "v2.56.0.windows.3/", "type": "dir"},
            {"name": "v2.56.1.windows.1/", "type": "dir"},
            {"name": "v2.55.0.windows.1/", "type": "dir"},
            {"name": "untagged-abc/", "type": "dir"},
        ]
        release = parse_git_mirror_listing(data, Arch.X64)
        assert release is not None
        self.assertEqual(release.version, "2.56.1")

    def test_python_release_filtering(self) -> None:
        data = [
            {"name": "Python 3.16.0a1", "pre_release": True},
            {"name": "Python 3.15.2", "pre_release": False},
            {"name": "Python 3.15.1", "pre_release": False},
            {"name": "Python 3.14.10", "pre_release": False},
        ]
        release = parse_python_releases(data, Arch.X64, "3.15")
        assert release is not None
        self.assertEqual(release.version, "3.15.2")
        self.assertEqual(release.filename, "python-3.15.2-amd64.exe")

        old = parse_python_releases(data, Arch.X64, "3.14")
        assert old is not None
        self.assertEqual(old.version, "3.14.10")

    def test_python_mirror_listing(self) -> None:
        data = [
            {"name": "3.15.2/", "type": "dir"},
            {"name": "3.15.1/", "type": "dir"},
            {"name": "3.14.10/", "type": "dir"},
        ]
        release = parse_python_mirror_listing(data, Arch.ARM64, "3.15")
        assert release is not None
        self.assertEqual(release.version, "3.15.2")
        self.assertEqual(release.filename, "python-3.15.2-arm64.exe")

    def test_vscode_update(self) -> None:
        data = {
            "productVersion": "1.146.0",
            "url": "https://example/VSCodeUserSetup-x64-1.146.0.exe",
            "sha256hash": "abc123",
        }
        release = parse_vscode_update(data, Arch.X64)
        assert release is not None
        self.assertEqual(release.version, "1.146.0")
        self.assertEqual(release.filename, "VSCodeUserSetup-x64-1.146.0.exe")
        self.assertEqual(release.urls[0].sha256, "abc123")

    def test_claude_npm(self) -> None:
        release = parse_claude_npm({"version": "2.1.322"})
        assert release is not None
        self.assertEqual(release.version, "2.1.322")
        self.assertEqual(release.urls, ())

    def test_pinned(self) -> None:
        node = pinned_release(ComponentId.NODE, Arch.X64)
        assert node is not None and node.is_pinned
        self.assertEqual(node.version, "26.13.1")

        python = pinned_release(ComponentId.PYTHON, Arch.X64, "3.15")
        assert python is not None
        self.assertEqual(python.version, "3.15.2")

        exact = pinned_release(ComponentId.PYTHON, Arch.X64, "3.13.9")
        assert exact is not None
        self.assertEqual(exact.version, "3.13.9")

        self.assertIsNone(pinned_release(ComponentId.PYTHON, Arch.X64, "3.13"))

        vscode = pinned_release(ComponentId.VSCODE, Arch.X64)
        assert vscode is not None
        self.assertIn("1.146.0", vscode.urls[0].url)

    def test_sources(self) -> None:
        self.assertEqual(_sources("auto"), ["official", "mirror"])
        self.assertEqual(_sources("official"), ["official"])
        self.assertEqual(_sources("mirror"), ["mirror"])

    def test_cache_variant(self) -> None:
        with tempfile.TemporaryDirectory(prefix="claudeenv-cache-") as temp:
            cache = ReleaseCache(Path(temp) / "releases.json")
            from app.models import ReleaseInfo, UrlCandidate

            normal = ReleaseInfo(
                ComponentId.PYTHON,
                "3.15.2",
                (3, 15, 2),
                "python-3.15.2-amd64.exe",
                (UrlCandidate("https://example/python.exe", "official", "abc"),),
            )
            cache.set(ComponentId.PYTHON, Arch.X64, normal, "3.15")
            self.assertIsNotNone(cache.get(ComponentId.PYTHON, Arch.X64, 6, "3.15"))
            self.assertIsNone(cache.get(ComponentId.PYTHON, Arch.X64, 6, "3.13"))


if __name__ == "__main__":
    unittest.main()
