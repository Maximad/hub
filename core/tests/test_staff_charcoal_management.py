"""Charcoal management overview contracts: Hub-only finance, inventory, reports."""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class StaffCharcoalManagementContracts(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        root = Path(settings.BASE_DIR)
        cls.finance = (root / "templates/staff/finance_home.html").read_text(encoding="utf-8")
        cls.inventory = (root / "templates/staff/inventory_home.html").read_text(encoding="utf-8")
        cls.reports = (root / "templates/staff/reports_home.html").read_text(encoding="utf-8")
        cls.finance_base = (root / "templates/staff/finance_base.html").read_text(encoding="utf-8")
        cls.inventory_base = (root / "templates/staff/inventory_base.html").read_text(encoding="utf-8")
        cls.css = (root / "static/css/staff_charcoal.css").read_text(encoding="utf-8")

    def test_only_three_overview_pages_opt_into_dark_theme(self):
        marker = "{% block body_class %}staff-charcoal-management{% endblock %}"
        for source in (self.finance, self.inventory, self.reports):
            self.assertIn(marker, source)
        for source in (self.finance_base, self.inventory_base):
            self.assertNotIn(marker, source)
        self.assertIn("body.hub-staff-ui.hub-ui-v3.staff-charcoal-management {", self.css)

    def test_financial_figures_and_business_day_context_unchanged(self):
        for marker in (
            "finance.opening_cash_syp", "finance.expected_cash_syp",
            "finance.cash_difference_syp", "finance.cash_expenses_syp",
            "sums.cash_total", "sums.non_cash_sales_syp",
            "finance.non_sales_cash_in_syp", "finance.cash_out_syp",
            "report_date", "format_syp", "staff_daily_closes",
            "staff_finance_cashbox_csv",
        ):
            self.assertIn(marker, self.finance)
        self.assertIn("تاريخ العمل المحدد، وليس بالضرورة رصيد الصندوق الحالي", self.finance)

    def test_inventory_permissions_and_non_fungible_stock_workflows_preserved(self):
        for marker in (
            "active_count", "low_items|length", "waste_value|format_syp",
            "inventory_can_manage", "staff_caps.inventory_stock_count",
            "staff_inventory_stock_count", "staff_inventory_purchase_new",
            "staff_inventory_consumption_requests", "unpaid_purchases",
            "p.remaining_syp|format_syp",
        ):
            self.assertIn(marker, self.inventory)

    def test_reports_use_recorded_data_and_do_not_create_transactions(self):
        for marker in (
            "sums.order_total|format_syp", "sums.orders_count",
            "sums.remaining_total|format_syp", "sums.avg_order_value|format_syp",
            "sums.estimated_gross_margin_syp|format_syp",
            "sums.products_missing_cost_count", "staff_reports_day",
            "staff_product_margin_report",
        ):
            self.assertIn(marker, self.reports)
        self.assertIn("تصحيح الدفعات أو إغلاق الفترة", self.reports)

    def test_visual_scope_and_accessibility(self):
        for marker in (
            "staff-charcoal-management .hub-ux5-kpi",
            "staff-charcoal-management a.hub-ux5-kpi",
            "staff-charcoal-management .hub-ux5-caution",
            ":focus-visible", "prefers-reduced-motion:reduce", "@media print",
        ):
            self.assertIn(marker, self.css)
        self.assertNotIn("pointer-events:none!important", self.css)
