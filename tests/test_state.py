from __future__ import annotations

import unittest

from app import components
from app.models import (
    ComponentId,
    DetectionResult,
    OverallState,
    ReleaseInfo,
    Requirement,
    Status,
)


def result(cid: ComponentId, version: tuple[int, ...] | None, status: Status = Status.OK) -> DetectionResult:
    return DetectionResult(
        component=cid,
        status=status,
        installed_version=".".join(str(part) for part in version) if version else None,
        version_tuple=version,
        location=r"D:\App",
    )


def release(cid: ComponentId, version: tuple[int, ...]) -> ReleaseInfo:
    return ReleaseInfo(cid, ".".join(str(part) for part in version), version, "x.exe", ())


class StateTests(unittest.TestCase):
    def test_missing_stays_missing(self) -> None:
        applied = components.apply_release(
            result(ComponentId.CLAUDE, None, Status.MISSING),
            release(ComponentId.CLAUDE, (2, 1, 322)),
        )
        self.assertEqual(applied.status, Status.MISSING)

    def test_outdated_wins_over_update(self) -> None:
        applied = components.apply_release(
            result(ComponentId.NODE, (20, 11, 0)),
            release(ComponentId.NODE, (26, 13, 1)),
        )
        self.assertEqual(applied.status, Status.OUTDATED)

    def test_update_available(self) -> None:
        applied = components.apply_release(
            result(ComponentId.NODE, (24, 22, 1)),
            release(ComponentId.NODE, (26, 13, 1)),
        )
        self.assertEqual(applied.status, Status.UPDATE_AVAILABLE)

    def test_equal_or_newer_is_ok(self) -> None:
        equal = components.apply_release(
            result(ComponentId.GIT, (2, 56, 1)),
            release(ComponentId.GIT, (2, 56, 1)),
        )
        newer = components.apply_release(
            result(ComponentId.CLAUDE, (2, 1, 322)),
            release(ComponentId.CLAUDE, (2, 1, 318)),
        )
        self.assertEqual(equal.status, Status.OK)
        self.assertEqual(newer.status, Status.OK)

    def test_overall_ready(self) -> None:
        results = {
            ComponentId.CLAUDE: result(ComponentId.CLAUDE, (2, 1, 319)),
            ComponentId.GIT: result(ComponentId.GIT, (2, 56, 0)),
            ComponentId.NODE: result(ComponentId.NODE, (24, 22, 1), Status.UPDATE_AVAILABLE),
            ComponentId.PYTHON: result(ComponentId.PYTHON, (3, 15, 1)),
            ComponentId.VSCODE: result(ComponentId.VSCODE, (1, 145, 3)),
        }
        overall = OverallState.from_results(results, components.requirements())
        self.assertEqual(overall.level, "ready")
        self.assertEqual(overall.required_ok, 1)
        self.assertEqual(overall.recommended_ok, 2)

    def test_overall_basic_and_not_ready(self) -> None:
        results = {
            ComponentId.CLAUDE: result(ComponentId.CLAUDE, (2, 1, 319)),
            ComponentId.GIT: result(ComponentId.GIT, None, Status.MISSING),
            ComponentId.NODE: result(ComponentId.NODE, (24, 22, 1)),
            ComponentId.PYTHON: result(ComponentId.PYTHON, None, Status.MISSING),
            ComponentId.VSCODE: result(ComponentId.VSCODE, None, Status.MISSING),
        }
        overall = OverallState.from_results(results, components.requirements())
        self.assertEqual(overall.level, "basic")

        results[ComponentId.CLAUDE] = result(ComponentId.CLAUDE, None, Status.MISSING)
        overall = OverallState.from_results(results, components.requirements())
        self.assertEqual(overall.level, "not_ready")

    def test_requirements_include_all(self) -> None:
        requirements = components.requirements()
        self.assertEqual(requirements[ComponentId.CLAUDE], Requirement.REQUIRED)
        self.assertEqual(requirements[ComponentId.NODE], Requirement.RECOMMENDED)
        self.assertEqual(requirements[ComponentId.VSCODE], Requirement.OPTIONAL)


if __name__ == "__main__":
    unittest.main()
