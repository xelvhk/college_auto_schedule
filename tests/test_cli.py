from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from rasp.cli import main


FIXTURES = Path(__file__).parent / "fixtures"


class ImportCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "rasp.sqlite3"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def run_cli(self, *arguments: str) -> tuple[int, dict[str, object]]:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(arguments)
        return exit_code, json.loads(output.getvalue())

    def test_import_then_status_reports_active_version(self) -> None:
        exit_code, imported = self.run_cli(
            "import",
            str(FIXTURES / "valid-import.xlsx"),
            "--database",
            str(self.database_path),
        )
        status_code, status = self.run_cli(
            "status", "--database", str(self.database_path)
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(imported["versionId"], 1)
        self.assertEqual(status_code, 0)
        self.assertEqual(status["activeVersionId"], 1)
        self.assertEqual(status["counts"]["teachers"], 1)
        self.assertEqual(status["counts"]["students"], 1)
        self.assertEqual(status["counts"]["rooms"], 1)
        self.assertEqual(status["counts"]["academicYears"], 1)
        self.assertEqual(status["counts"]["academicCycles"], 1)
        self.assertEqual(status["counts"]["calendarPeriods"], 1)
        self.assertEqual(status["counts"]["bellSlots"], 1)
        self.assertEqual(status["counts"]["calendarExceptions"], 1)
        self.assertEqual(status["counts"]["resourceUnavailability"], 1)

    def test_repeated_file_returns_reused_version(self) -> None:
        self.run_cli(
            "import",
            str(FIXTURES / "valid-import.xlsx"),
            "--database",
            str(self.database_path),
        )

        exit_code, result = self.run_cli(
            "import",
            str(FIXTURES / "valid-import.xlsx"),
            "--database",
            str(self.database_path),
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(result["reused"])
        self.assertEqual(result["versionId"], 1)

    def test_invalid_workbook_does_not_create_database(self) -> None:
        exit_code, result = self.run_cli(
            "import",
            str(FIXTURES / "formula-import.xlsx"),
            "--database",
            str(self.database_path),
        )

        self.assertEqual(exit_code, 2)
        self.assertFalse(result["valid"])
        self.assertFalse(self.database_path.exists())

    def test_missing_workbook_is_reported_as_import_validation_error(self) -> None:
        exit_code, result = self.run_cli(
            "import",
            str(Path(self.temporary_directory.name) / "missing.xlsx"),
            "--database",
            str(self.database_path),
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(result["issues"][0]["code"], "file_not_found")
        self.assertFalse(self.database_path.exists())

    def test_activate_version_command_keeps_known_version_active(self) -> None:
        self.run_cli(
            "import",
            str(FIXTURES / "valid-import.xlsx"),
            "--database",
            str(self.database_path),
        )

        exit_code, result = self.run_cli(
            "activate-version",
            "1",
            "--database",
            str(self.database_path),
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(result["activeVersionId"], 1)

    def test_activate_unknown_version_returns_stable_error(self) -> None:
        self.run_cli(
            "import",
            str(FIXTURES / "valid-import.xlsx"),
            "--database",
            str(self.database_path),
        )

        exit_code, result = self.run_cli(
            "activate-version",
            "404",
            "--database",
            str(self.database_path),
        )

        self.assertEqual(exit_code, 4)
        self.assertEqual(result["error"]["code"], "version_not_found")

    def test_readiness_reports_active_version(self) -> None:
        self.run_cli(
            "import",
            str(FIXTURES / "valid-import.xlsx"),
            "--database",
            str(self.database_path),
        )

        exit_code, result = self.run_cli(
            "readiness", "--database", str(self.database_path)
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(result["isReady"])
        self.assertEqual(result["errorCount"], 0)
        self.assertEqual(result["warningCount"], 0)
        self.assertEqual(result["issues"], [])

    def test_readiness_without_active_version_returns_stable_error(self) -> None:
        exit_code, result = self.run_cli(
            "readiness", "--database", str(self.database_path)
        )

        self.assertEqual(exit_code, 5)
        self.assertEqual(result["error"]["code"], "no_active_import")

    def test_solver_problem_reports_deterministic_active_problem(self) -> None:
        self.run_cli(
            "import",
            str(FIXTURES / "valid-import.xlsx"),
            "--database",
            str(self.database_path),
        )

        exit_code, result = self.run_cli(
            "solver-problem", "--database", str(self.database_path)
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(result["isReady"])
        self.assertEqual(result["workloadCount"], 1)
        self.assertEqual(result["lessonDemandCount"], 36)
        self.assertEqual(result["eligibleWeekCount"], 9)
        self.assertEqual(result["placementDomainCount"], 1)
        self.assertEqual(result["placementOptionCount"], 50)
        self.assertEqual(result["demandSamples"][0]["demandCode"], "W-001#001")

    def test_solver_problem_without_active_version_returns_stable_error(self) -> None:
        exit_code, result = self.run_cli(
            "solver-problem", "--database", str(self.database_path)
        )

        self.assertEqual(exit_code, 5)
        self.assertEqual(result["error"]["code"], "no_active_import")


if __name__ == "__main__":
    unittest.main()
