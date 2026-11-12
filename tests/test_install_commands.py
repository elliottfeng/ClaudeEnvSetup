from __future__ import annotations

import unittest
from unittest import mock

from app import install
from app.models import ComponentId, ReleaseInfo


def make_release(cid: ComponentId, filename: str = "installer.exe") -> ReleaseInfo:
    return ReleaseInfo(cid, "1.2.3", (1, 2, 3), filename, ())


class InstallCommandTests(unittest.TestCase):
    def test_command_line_quotes_exe(self) -> None:
        self.assertEqual(
            install.command_line(r"C:\Program Files\App\app.exe", "/S"),
            r'"C:\Program Files\App\app.exe" /S',
        )
        self.assertEqual(install.command_line("msiexec.exe", "/i a.msi"), "msiexec.exe /i a.msi")

    def test_node_plan(self) -> None:
        plan = install.build_node_plan(
            make_release(ComponentId.NODE),
            r"D:\下载 目录\node.msi",
            r"D:\安装 目录\Node.js",
            r"D:\logs\node.log",
        )
        step = plan.steps[0]
        self.assertEqual(step.exe, "msiexec.exe")
        self.assertTrue(step.needs_admin)
        self.assertIn('INSTALLDIR="D:\\安装 目录\\Node.js"', step.parameters)
        self.assertIn('/i "D:\\下载 目录\\node.msi"', step.parameters)
        self.assertIn("/qn", step.parameters)
        self.assertIn(3010, step.success_codes)

    def test_git_plan_dir_last(self) -> None:
        plan = install.build_git_plan(
            make_release(ComponentId.GIT, "Git-1.2.3-64-bit.exe"),
            r"D:\下载 目录\Git.exe",
            r"D:\安装 目录\Git",
            r"D:\logs\git.log",
        )
        step = plan.steps[0]
        self.assertTrue(step.parameters.startswith("/VERYSILENT"))
        self.assertTrue(step.parameters.endswith('/DIR="D:\\安装 目录\\Git"'))
        self.assertIn("/LOG=", step.parameters)
        self.assertTrue(step.needs_admin)

    def test_python_user_and_all_users(self) -> None:
        user = install.build_python_plan(
            make_release(ComponentId.PYTHON),
            r"D:\python.exe",
            r"D:\Python315",
            r"D:\logs\python.log",
            all_users=False,
        )
        self.assertIn("InstallAllUsers=0", user.steps[0].parameters)
        self.assertIn("InstallLauncherAllUsers=0", user.steps[0].parameters)
        self.assertFalse(user.steps[0].needs_admin)

        shared = install.build_python_plan(
            make_release(ComponentId.PYTHON),
            r"D:\python.exe",
            r"C:\Program Files\Python315",
            r"D:\logs\python.log",
            all_users=True,
        )
        self.assertIn("InstallAllUsers=1", shared.steps[0].parameters)
        self.assertTrue(shared.steps[0].needs_admin)

    def test_vscode_plan_dir_last(self) -> None:
        plan = install.build_vscode_plan(
            make_release(ComponentId.VSCODE),
            r"D:\VSCodeSetup.exe",
            r"D:\安装 目录\VS Code",
            r"D:\logs\vscode.log",
        )
        step = plan.steps[0]
        self.assertFalse(step.needs_admin)
        self.assertIn("MERGETASKS=!runcode,addtopath", step.parameters)
        self.assertTrue(step.parameters.endswith('/DIR="D:\\安装 目录\\VS Code"'))

    @mock.patch("app.install.shutil.which")
    def test_claude_plan_with_winget_and_npm(self, which_mock) -> None:
        which_mock.side_effect = lambda name: {
            "winget": r"C:\Tools\winget.exe",
            "npm": r"C:\Program Files\nodejs\npm.CMD",
        }.get(name)
        plan = install.build_claude_plan(r"D:\logs\claude.log")
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].exe, r"C:\Tools\winget.exe")
        self.assertIn("Anthropic.ClaudeCode", plan.steps[0].parameters)
        self.assertEqual(len(plan.fallbacks), 2)
        self.assertIn("install -g @anthropic-ai/claude-code", plan.fallbacks[0].parameters)
        self.assertIn("--registry=", plan.fallbacks[1].parameters)
        self.assertFalse(plan.requires_download)

        upgrade = install.build_claude_plan(r"D:\logs\claude.log", prefer_npm=True)
        self.assertEqual(len(upgrade.steps), 2)
        self.assertIn("install -g @anthropic-ai/claude-code", upgrade.steps[0].parameters)
        self.assertIn("--registry=", upgrade.steps[1].parameters)
        self.assertEqual(upgrade.fallbacks[0].exe, r"C:\Tools\winget.exe")

    @mock.patch("app.install.shutil.which", return_value=None)
    def test_claude_plan_without_tools(self, _which_mock) -> None:
        plan = install.build_claude_plan(None)
        self.assertEqual(plan.steps, ())

    def test_interpret_exit(self) -> None:
        step = install.CommandStep(
            "x.exe", "", False, "test", frozenset({0, 3010})
        )
        self.assertTrue(install._interpret_exit(step, 0, "").success)
        reboot = install._interpret_exit(step, 3010, "")
        self.assertTrue(reboot.success)
        self.assertTrue(reboot.needs_reboot)
        failed = install._interpret_exit(step, 1603, "")
        self.assertFalse(failed.success)
        cancelled = install._interpret_exit(step, None, "已取消管理员授权")
        self.assertTrue(cancelled.cancelled)


if __name__ == "__main__":
    unittest.main()
