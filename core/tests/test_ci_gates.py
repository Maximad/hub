"""CI event-state contract: fast drafts must not replace the release gate."""
from pathlib import Path
from django.conf import settings
from django.test import SimpleTestCase


class CIWorkflowGatesTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workflow = (Path(settings.BASE_DIR) / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    def test_ready_prs_and_main_still_run_full_postgresql_gate(self):
        src = self.workflow
        self.assertIn("ready_for_review", src)
        self.assertIn("synchronize", src)
        self.assertIn("converted_to_draft", src)
        self.assertIn("if: ${{ github.event_name != 'pull_request' || github.event.pull_request.draft == false }}", src)
        self.assertIn("Run full Django test suite on PostgreSQL", src)
        self.assertIn("Build release image with immutable static assets", src)
        self.assertIn("Verify consumption mobile layout", src)
        self.assertIn("Verify public charcoal discovery layout", src)

    def test_draft_checks_lightweight_and_publishing_remains_main_only(self):
        src = self.workflow
        self.assertIn("fast-checks:", src)
        self.assertIn("if: ${{ github.event_name == 'pull_request' && github.event.pull_request.draft == true }}", src)
        self.assertIn("python manage.py check", src)
        self.assertIn("Lightweight source and UI contract tests (no database)", src)
        self.assertIn("core.tests.test_public_charcoal.PublicCharcoalStaticContracts", src)
        self.assertIn("name: PostgreSQL checks and tests", src)
        self.assertIn("needs: postgres-tests", src)
        self.assertIn("if: github.event_name == 'push' && github.ref == 'refs/heads/main'", src)
