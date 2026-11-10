"""Public charcoal: presentation-only contracts for the live Hub guest surfaces."""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse


class PublicCharcoalStaticContracts(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.root = Path(settings.BASE_DIR)

    @classmethod
    def source(cls, path):
        return (cls.root / path).read_text(encoding="utf-8")

    def test_public_theme_loaded_after_homepage_and_configured_theme(self):
        base = self.source("templates/base.html")
        self.assertIn("{% static 'css/public_charcoal.css' %}", base)
        self.assertGreater(base.index("public_charcoal.css"), base.index("{% block extra_head %}"))
        self.assertGreater(base.index("public_charcoal.css"), base.index('includes/hub_theme.html'))
        self.assertIn('request.path|slice:":7" != "/staff/"', base)
        self.assertIn('request.path|slice:":10" != "/provider/"', base)
        public_base = self.source("templates/public/base.html")
        self.assertIn("public_homepage.css", public_base)
        self.assertIn("public-home", public_base)

    def test_homepage_stays_enabled_or_disabled_only_by_existing_release_setting(self):
        homepage_view = self.source("core/views/public_homepage.py")
        settings_source = self.source("config/settings.py")
        self.assertIn("if not settings.PUBLIC_HOMEPAGE_ENABLED:", homepage_view)
        self.assertIn("return menu.dashboard(request)", homepage_view)
        self.assertIn("return render(request, 'public/homepage.html'", homepage_view)
        self.assertIn("PUBLIC_HOMEPAGE_ENABLED = bool_env(", settings_source)
        for path in ("templates/public/homepage.html", "templates/public/events.html",
                     "templates/public/event_detail.html", "templates/public/discovery_list.html"):
            self.assertIn("public/base.html", self.source(path))

    def test_css_is_scoped_away_from_staff_provider_and_transaction_logic(self):
        css = self.source("static/css/public_charcoal.css")
        for marker in (
            "body.hub-page.public-home", "body.hub-page.public-menu-page",
            "body.hub-page.customer-space-page", "body.hub-page.table-entry-page",
            "body.hub-page.wifi-entry-page", "body.hub-page.public-member-page",
            "prefers-reduced-motion:reduce", "@media print", ":focus-visible",
            "--pc-teal:#24C6B2", "--pc-bg:#15181C",
        ):
            self.assertIn(marker, css)
        self.assertNotIn("body.hub-staff-ui", css)
        self.assertNotIn("body.hub-provider-ui", css)
        self.assertNotIn("pointer-events:none!important", css)
        self.assertNotIn("display:none!important", css)
        self.assertEqual(css.count("{"), css.count("}"))

    def test_wifi_portal_remains_offline_and_security_sensitive_forms_unchanged(self):
        for path in ("templates/menu/wifi_entry.html", "templates/menu/wifi_staff_login.html"):
            src = self.source(path)
            self.assertIn("{% static 'css/public_charcoal.css' %}", src)
            self.assertGreater(src.index("public_charcoal.css"), src.index('includes/hub_theme.html'))
            self.assertNotIn("fonts.googleapis.com", src)
            self.assertNotIn("fonts.gstatic.com", src)
            self.assertIn("{% csrf_token %}", src)
        wifi = self.source("templates/menu/wifi_entry.html")
        self.assertIn('data-wifi-internet-open', wifi)
        self.assertIn('name="wifi_action"', wifi)
        self.assertIn("wifi-sheet", wifi)
        transition = self.source("templates/menu/hotspot_connect.html")
        for token in ("id=\"hotspot-login\"", 'action="{{ login_url }}"',
                      'name="username"', 'name="password"', 'name="dst"',
                      "document.getElementById('hotspot-login').submit()"):
            self.assertIn(token, transition)

    def test_menu_visit_member_and_order_forms_keep_existing_contracts(self):
        menu = self.source("templates/menu/menu.html")
        visit = self.source("templates/menu/current_visit.html")
        account = self.source("templates/member_accounts/home.html")
        member_login = self.source("templates/member_accounts/login.html")
        invitation = self.source("templates/member_accounts/join.html")
        for token in ('id="menu-order-form"', "menu_cart.js", "hub_cart_common.js",
                      "data-cart-sheet", "data-category-nav", "{% csrf_token %}"):
            self.assertIn(token, menu)
        for token in ('name="action" value="bill"', 'name="action" value="assistance"',
                      "{% csrf_token %}", "current_visit_checkout_qr"):
            self.assertIn(token, visit)
        self.assertIn('id="member-card-qr"', account)
        self.assertIn('name="phone"', member_login)
        self.assertIn("{% csrf_token %}", invitation)
        self.assertIn('name="confirm" value="yes"', invitation)
        self.assertIn("public-member-page", account)
        self.assertIn("public-member-page", member_login)

    def test_critical_text_pairs_meet_aa_normal_text_contrast(self):
        def lightness(hex_code):
            values = [int(hex_code[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4
                      for v in values]
            return sum(x * w for x, w in zip(linear, (.2126, .7152, .0722)))

        for foreground, background in (
            ("#F3F1EC", "#1E2329"),
            ("#A9B0B8", "#262C33"),
            ("#10201F", "#24C6B2"),
            ("#10201F", "#169D8D"),
            ("#B3EFD1", "#204337"),
        ):
            values = sorted((lightness(foreground), lightness(background)), reverse=True)
            with self.subTest(foreground=foreground, background=background):
                self.assertGreaterEqual((values[0] + .05) / (values[1] + .05), 4.5)


@override_settings(ALLOWED_HOSTS=["testserver"], SECURE_SSL_REDIRECT=False,
                   PUBLIC_HOMEPAGE_ENABLED=True)
class PublicCharcoalRenderedContracts(TestCase):
    def test_homepage_and_menu_link_public_theme(self):
        for url_name in ("dashboard", "menu_public"):
            with self.subTest(page=url_name):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "public_charcoal.css")
                self.assertNotContains(response, "staff_charcoal.css")
        response = self.client.get(reverse("dashboard"))
        html = response.content.decode("utf-8")
        self.assertLess(html.index("public_homepage.css"), html.index("public_charcoal.css"))

    def test_wifi_welcome_theme_is_local_and_public(self):
        response = self.client.get(reverse("wifi_entry"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "public_charcoal.css")
        self.assertContains(response, "wifi-welcome")
        self.assertNotContains(response, "fonts.googleapis.com")
