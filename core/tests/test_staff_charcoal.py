"""Charcoal Creative Control Room: presentation-only integration contract.

These are inexpensive source checks. Browser and PostgreSQL regression testing
are still required before merging or deploying the theme.
"""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class StaffCharcoalContractTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        root = Path(settings.BASE_DIR)
        cls.base = (root / "templates/base.html").read_text(encoding="utf-8")
        cls.nav = (root / "templates/includes/staff_shell_nav.html").read_text(encoding="utf-8")
        cls.operations = (root / "templates/staff/home.html").read_text(encoding="utf-8")
        cls.css = (root / "static/css/staff_charcoal.css").read_text(encoding="utf-8")
        cls.js = (root / "static/js/staff_charcoal.js").read_text(encoding="utf-8")

    def test_staff_only_styles_loaded_after_existing_design(self):
        staff_guard = '{% if request.user.is_authenticated and request.path|slice:":7" == "/staff/" %}'
        self.assertIn(staff_guard + "\n  <link rel=\"stylesheet\" href=\"{% static 'css/staff_charcoal.css' %}\">", self.base)
        self.assertGreater(self.base.index("staff_charcoal.css"), self.base.index("staff_operations_designed.css"))
        self.assertGreater(self.base.index("staff_charcoal.css"), self.base.index('includes/hub_theme.html'))
        self.assertIn("staff_charcoal.js", self.base)
        self.assertIn('<meta name="theme-color" content="#15181C">', self.base)
        self.assertIn('class="hub-page hub-ui-v3 ', self.base)

    def test_palette_scope_and_accessibility(self):
        self.assertIn("body.hub-staff-ui {", self.css)
        for token in ("#15181C", "#1E2329", "#262C33", "#343C45",
                      "#F3F1EC", "#A9B0B8", "#24C6B2", "#D88A3D"):
            self.assertIn(token, self.css)
        self.assertIn("body.staff-workspace-page.hub-staff-ui .ops-designed", self.css)
        self.assertIn(":focus-visible", self.css)
        self.assertIn("@media(prefers-reduced-motion:reduce)", self.css)
        self.assertIn("@media print", self.css)
        self.assertIn("staff-focus-mode", self.css)
        self.assertNotIn("url(http", self.css)

    def test_role_gated_navigation_and_workflows_not_replaced(self):
        for gate in ("staff_caps.pos", "staff_caps.kitchen_board",
                     "staff_caps.cashier", "staff_caps.internet_billing",
                     "staff_caps.finance"):
            self.assertIn(gate, self.nav)
        self.assertIn("staff/_context_drawer_shell.html", self.base)
        self.assertIn("staff_responsive_tables.js", self.base)
        self.assertIn("staff_notifications.js", self.base)
        self.assertIn('data-operations-v2', self.operations)
        self.assertIn('id="active-orders"', self.operations)
        self.assertIn('ops-dashboard-order-grid', self.operations)
        self.assertIn('ops-dashboard-filter', self.operations)
        self.assertIn('data-guest-wifi-pin', self.operations)

    def test_focus_mode_is_keyboard_accessible_and_presentation_only(self):
        self.assertIn('type="button" class="staff-charcoal-focus"', self.nav)
        self.assertIn('data-staff-focus-toggle aria-pressed="false"', self.nav)
        self.assertIn('aria-label="تفعيل وضع التركيز"', self.nav)
        self.assertIn("setAttribute('aria-pressed'", self.js)
        self.assertIn("classList.toggle('staff-focus-mode'", self.js)
        self.assertIn("localStorage", self.js)
        for forbidden in ("fetch(", "XMLHttpRequest", "navigator.sendBeacon", "window.location=", "submit("):
            self.assertNotIn(forbidden, self.js)
