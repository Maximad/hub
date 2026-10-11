"""Suite-wide Hub UI 3.0 presentation contract.

Checks the entrypoints and protected workflows, rather than every CSS rule.
No operational database writes or live network dependencies.
"""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class HubUiV3SuiteContractTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.root = Path(settings.BASE_DIR)

    @classmethod
    def source(cls, path):
        return (cls.root / path).read_text(encoding="utf-8")

    def test_base_template_applies_v3_to_all_customer_staff_provider_paths(self):
        base = self.source("templates/base.html")
        self.assertIn('class="hub-page hub-ui-v3 ', base)
        self.assertIn("{% static 'css/hub_ui_v3_suite.css' %}", base)
        self.assertIn("{% static 'css/hub_ui_v3_pilot.css' %}", base)
        self.assertGreater(base.index("hub_ui_v3_suite.css"), base.index("hub_ui_v3_pilot.css"))
        self.assertGreater(base.index("hub_ui_v3_suite.css"), base.index("staff_ui_v2_contrast_fix.css"))
        self.assertGreater(base.index("hub_ui_v3_suite.css"), base.index("customer_experience_v2.css"))
        self.assertIn("IBM+Plex+Sans+Arabic", base)
        self.assertIn("onload=\"this.media='all'\"", base)

    def test_design_tokens_are_scoped_and_roles_have_distinct_accents(self):
        css = self.source("static/css/hub_ui_v3_suite.css")
        self.assertIn("body.hub-ui-v3 {", css)
        for token in ("--hub-v3-turquoise","#1abc9c","#e67e22","#9b59b6","#f1c40f",
                      "--hub-v3-canvas","#f7f8f8","--hub-v3-primary","#137f70"):
            self.assertIn(token, css)
        self.assertIn("body.hub-staff-ui.hub-ui-v3", css)
        self.assertIn("body.hub-provider-ui.hub-ui-v3", css)
        self.assertIn("body.hub-ui-v3.wifi-entry-page", css)
        self.assertIn("color:#fff", css)
        self.assertIn(":focus-visible", css)
        self.assertIn("@media print", css)
        self.assertIn("@media(prefers-reduced-motion:reduce)", css)

    def test_staff_navigation_roles_and_real_ordering_forms_unchanged(self):
        base = self.source("templates/base.html")
        nav = self.source("templates/includes/staff_shell_nav.html")
        pos = self.source("templates/staff/pos.html")
        menu = self.source("templates/menu/menu.html")
        self.assertIn("staff_caps.pos", nav)
        self.assertIn("staff_caps.internet_billing", nav)
        self.assertIn("staff_caps.finance", nav)
        self.assertIn("staff-context-drawer", self.source("templates/staff/home.html"))
        self.assertIn('id="menu-order-form"', pos)
        self.assertIn('id="menu-order-form"', menu)
        self.assertIn("data-cart-sheet", menu)
        self.assertIn("data-category-nav", menu)
        self.assertIn("data-customer-cart", self.source("static/js/customer_space.js"))
        self.assertIn("staff_responsive_tables.js", base)
        self.assertIn("system_settings.brand_icon_url", base)
        self.assertIn("system_settings.brand_logo_url", menu)

    def test_provider_uses_the_same_django_permissions_and_forms(self):
        provider = self.source("templates/provider/base.html")
        login = self.source("templates/provider/login.html")
        dashboard = self.source("templates/provider/dashboard.html")
        self.assertIn('{% extends "base.html" %}', provider)
        self.assertIn("provider-portal", provider)
        self.assertIn("{% csrf_token %}", login)
        self.assertIn("provider_caps.internet_view_reports", dashboard)
        self.assertIn("provider_caps.internet_view_network", dashboard)

    def test_admin_stays_in_django_admin_shell_with_own_styling(self):
        admin = self.source("templates/admin/base_site.html")
        css = self.source("static/admin/css/hub_admin_v3.css")
        self.assertIn('{% extends "admin/base_site.html" %}', admin)
        self.assertIn("admin/css/hub_admin.css", admin)
        self.assertIn("admin/css/product_visibility_source.css", admin)
        self.assertIn("admin/js/product_visibility_source.js", admin)
        self.assertIn("admin/css/hub_admin_v3.css", admin)
        self.assertIn("{% block bodyclass %}", admin)
        self.assertIn("hub-admin-v3", css)
        self.assertIn("IBM Plex Sans Arabic", css)
        self.assertIn("@media print", css)

    def test_wifi_portal_uses_only_local_css_not_network_font(self):
        for path in ("templates/menu/wifi_entry.html", "templates/menu/wifi_staff_login.html"):
            src = self.source(path)
            self.assertIn("hub-ui-v3 wifi-entry-page", src)
            self.assertIn("css/hub_ui_v3_suite.css", src)
            self.assertIn("css/internet_experience.css", src)
            self.assertNotIn("fonts.googleapis.com", src)
        self.assertIn("wifi-welcome__shell", self.source("templates/menu/wifi_entry.html"))
        self.assertIn("{% csrf_token %}", self.source("templates/menu/wifi_staff_login.html"))

    def test_member_login_and_account_are_presentation_only(self):
        login = self.source("templates/member_accounts/login.html")
        home = self.source("templates/member_accounts/home.html")
        self.assertIn("hub-public-auth", login)
        self.assertIn('name="phone"', login)
        self.assertIn('name="next"', login)
        self.assertIn("hub-member-home__sections", home)
        self.assertIn('id="member-card-qr"', home)
        self.assertIn("member_card_qr", home)
        self.assertIn("member_account_logout", home)
        self.assertIn("{% csrf_token %}", home)
        self.assertEqual(home.count("{% block title %}"), 1)
        self.assertEqual(home.count("{% endblock %}"), 2)

    def test_existing_reports_data_is_preserved(self):
        report = self.source("templates/staff/reports_home.html")
        self.assertIn("hub-reports-v3__metrics", report)
        for field in ("sums.order_total","sums.cash_total","sums.remaining_total",
                      "sums.avg_order_value","sums.estimated_gross_margin_syp",
                      "sums.products_missing_cost_count"):
            self.assertIn(field, report)
        self.assertIn("staff_product_margin_report", report)
