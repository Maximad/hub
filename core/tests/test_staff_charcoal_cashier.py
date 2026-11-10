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


    def test_cashier_status_and_money_contrast(self):
        """Primary numbers, labels and warning text maintain AA contrast."""
        def luminance(code):
            rgb = [int(code[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [x / 12.92 if x <= 0.04045 else ((x + .055) / 1.055) ** 2.4 for x in rgb]
            return sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

        for foreground, background in (
            ("#F3F1EC", "#1E2329"),
            ("#A9B0B8", "#1E2329"),
            ("#10201F", "#24C6B2"),
            ("#FFE0AF", "#49392A"),
            ("#B3EFD1", "#204337"),
        ):
            light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
            with self.subTest(foreground=foreground, background=background):
                self.assertGreaterEqual((light + 0.05) / (dark + 0.05), 4.5)
