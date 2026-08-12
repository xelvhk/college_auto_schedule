from __future__ import annotations

from io import BytesIO
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from rasp.web.app import create_app


FIXTURES = Path(__file__).parent / "fixtures"
XLSX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


class WebApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "rasp.sqlite3"
        self.client = TestClient(create_app(database_path=self.database_path))

    def tearDown(self) -> None:
        self.client.close()
        self.temporary_directory.cleanup()

    def upload(self, endpoint: str, fixture: str, **kwargs: object):
        content = (FIXTURES / fixture).read_bytes()
        return self.client.post(
            endpoint,
            files={"file": (fixture, content, XLSX_CONTENT_TYPE)},
            **kwargs,
        )

    def test_home_page_is_accessible_and_hardened(self) -> None:
        response = self.client.get("/")
        stylesheet = self.client.get("/static/styles.css")
        script = self.client.get("/static/app.js")
        favicon = self.client.get("/static/favicon.svg")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(stylesheet.status_code, 200)
        self.assertEqual(script.status_code, 200)
        self.assertEqual(favicon.status_code, 200)
        self.assertIn("Импорт исходных данных", response.text)
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertIn("default-src 'self'", response.headers["content-security-policy"])

    def test_preview_returns_counts_and_does_not_create_database(self) -> None:
        response = self.upload("/api/imports/preview", "valid-import.xlsx")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["counts"]["teachers"], 1)
        self.assertEqual(payload["counts"]["specialties"], 1)
        self.assertEqual(payload["counts"]["curricula"], 1)
        self.assertEqual(payload["counts"]["disciplines"], 1)
        self.assertEqual(payload["samples"]["groups"][0]["groupCode"], "ИС-101")
        self.assertFalse(self.database_path.exists())

    def test_preview_returns_structured_validation_issues(self) -> None:
        response = self.upload("/api/imports/preview", "formula-import.xlsx")

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertFalse(payload["valid"])
        self.assertEqual(payload["issues"][0]["code"], "formula_forbidden")

    def test_activate_then_status_reports_active_version(self) -> None:
        activated = self.upload("/api/imports/activate", "valid-import.xlsx")
        status = self.client.get("/api/status")

        self.assertEqual(activated.status_code, 201)
        self.assertEqual(activated.json()["versionId"], 1)
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["activeVersionId"], 1)
        self.assertEqual(status.json()["counts"]["disciplines"], 1)

    def test_curriculum_mismatch_does_not_replace_active_version(self) -> None:
        self.upload("/api/imports/activate", "valid-import.xlsx")
        source = BytesIO((FIXTURES / "valid-import.xlsx").read_bytes())
        workbook = load_workbook(source)
        workbook["Нагрузка"]["D2"] = "UNKNOWN"
        changed = BytesIO()
        workbook.save(changed)
        workbook.close()

        rejected = self.client.post(
            "/api/imports/activate",
            files={"file": ("invalid-plan.xlsx", changed.getvalue(), XLSX_CONTENT_TYPE)},
        )
        status = self.client.get("/api/status")

        self.assertEqual(rejected.status_code, 422)
        self.assertEqual(
            rejected.json()["issues"][0]["code"],
            "discipline_not_in_curriculum",
        )
        self.assertEqual(status.json()["activeVersionId"], 1)
        self.assertEqual(len(status.json()["versions"]), 1)

    def test_saved_version_can_be_reactivated_and_unknown_version_is_404(self) -> None:
        self.upload("/api/imports/activate", "valid-import.xlsx")

        activated = self.client.post("/api/versions/1/activate")
        missing = self.client.post("/api/versions/404/activate")

        self.assertEqual(activated.status_code, 200)
        self.assertEqual(activated.json()["activeVersionId"], 1)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error"]["code"], "version_not_found")

    def test_cross_site_mutation_is_rejected(self) -> None:
        response = self.upload(
            "/api/imports/activate",
            "valid-import.xlsx",
            headers={"Origin": "https://malicious.example"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.database_path.exists())

    def test_oversized_upload_is_rejected_before_excel_parsing(self) -> None:
        response = self.client.post(
            "/api/imports/preview",
            files={
                "file": (
                    "large.xlsx",
                    b"0" * (10 * 1024 * 1024 + 1),
                    XLSX_CONTENT_TYPE,
                )
            },
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"]["code"], "file_too_large")


if __name__ == "__main__":
    unittest.main()
