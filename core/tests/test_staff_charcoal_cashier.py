"""Charcoal cashier overview contracts: presentation only, money flow unchanged."""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class StaffCharcoalCashierContracts(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        root = Path(settings.BASE_DIR)
        cls.cashier = (root / "templates/staff/cashier.html").read_text(encoding="utf-8")
        cls.visit = (root / "templates/staff/cashier_visit.html").read_text(encoding="utf-8")
        cls.order = (root / "templates/staff/cashier_order.html").read_text(encoding="utf-8")
        cls.css = (root / "static/css/staff_charcoal.css").read_text(encoding="utf-8")

    def test_cashier_overviews_explicitly_opt_in_but_order_payment_does_not(self):
        marker = "{% block body_class %}staff-charcoal-cashier{% endblock %}"
        self.assertIn(marker, self.cashier)
        self.assertIn(marker, self.visit)
        self.assertNotIn(marker, self.order)
        self.assertIn("body.hub-staff-ui.hub-ui-v3.staff-charcoal-cashier {", self.css)

    def test_cashier_index_counts_and_drawer_actions_preserved(self):
        for marker in (
            "visit_rows", "standalone_rows", "collect_count", "review_count",
            "row.remaining", "row.total", "row.paid", "format_syp",
            "data-staff-context-url", "staff_cashier_order",
            "staff_caps.visit_manage",
        ):
            self.assertIn(marker, self.cashier)

    def test_visit_payment_and_settlement_protection_preserved(self):
        for marker in (
            "active_metered_sessions", "staff_cashier_pay", "settle_close",
            "collection_request_key", "settlement_request_key",
            "idempotency_key", "{% csrf_token %}",
            "visit-manager-approval", "remaining", "total|format_syp",
        ):
            self.assertIn(marker, self.visit)
        self.assertIn("المبلغ النهائي", self.visit)

    def test_status_and_accessibility(self):
        for marker in (
            "staff-charcoal-cashier .hub-badge-success",
            "staff-charcoal-cashier .hub-badge-info",
            "staff-charcoal-cashier .hub-message-warning",
            "staff-charcoal-cashier .staff-order-panel__totals",
            ":focus-visible", "prefers-reduced-motion:reduce", "@media print",
        ):
            self.assertIn(marker, self.css)
        self.assertNotIn("pointer-events:none!important", self.css)
