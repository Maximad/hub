"""Read-only contracts for the scoped Hub Suite UI v3 visual pilot."""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class HubUiV3PilotContractTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        root = Path(settings.BASE_DIR)
        cls.base = (root / "templates/base.html").read_text(encoding="utf-8")
        cls.menu = (root / "templates/menu/menu.html").read_text(encoding="utf-8")
        cls.operations = (root / "templates/staff/home.html").read_text(encoding="utf-8")
        cls.css = (root / "static/css/hub_ui_v3_pilot.css").read_text(encoding="utf-8")

    def test_pilot_loads_after_existing_customer_and_staff_layers(self):
        self.assertIn("{% static 'css/hub_ui_v3_pilot.css' %}", self.base)
        self.assertGreater(
            self.base.index("hub_ui_v3_pilot.css"),
            self.base.index("customer_experience_v2.css"),
        )
        self.assertGreater(
            self.base.index("hub_ui_v3_pilot.css"),
            self.base.index("staff_ui_v2_contrast_fix.css"),
        )
        self.assertIn('request.path == "/staff/"', self.base)
        self.assertIn('request.path|slice:":6" == "/menu/"', self.base)

    def test_opt_in_is_only_on_menu_and_operations(self):
        self.assertIn("public-menu-page hub-ui-v3", self.menu)
        self.assertIn("staff-workspace-page staff-ui-v2-page hub-ui-v3", self.operations)
        self.assertIn("body.public-menu-page.hub-ui-v3", self.css)
        self.assertIn("body.staff-workspace-page.hub-ui-v3", self.css)

    def test_existing_logo_and_order_flow_are_preserved(self):
        self.assertIn("system_settings.brand_logo_url", self.menu)
        self.assertIn('id="menu-order-form"', self.menu)
        self.assertIn("data-category-nav", self.menu)
        self.assertIn("data-cart-sheet", self.menu)
        self.assertIn("data-menu-modal", self.menu)
        self.assertIn("data-submit-btn", self.menu)

    def test_category_features_use_live_sections_and_media(self):
        self.assertIn('aria-label="استكشف أقسام المنيو"', self.menu)
        self.assertIn('section_products|slice:":2"', self.menu)
        self.assertIn('href="#section-{{ section.id }}"', self.menu)
        self.assertIn('products|slice:":1"', self.menu)
        self.assertIn("product_card_image p.primary_media 320", self.menu)
        self.assertIn("system_settings.show_section_chips", self.menu)
        self.assertIn("system_settings.show_product_images", self.menu)
        self.assertIn('loading="lazy"', self.menu)

    def test_staff_context_and_capability_controls_remain(self):
        self.assertIn("workspace_caps.pos", self.operations)
        self.assertIn("workspace_caps.cashier", self.operations)
        self.assertIn("data-ops-search", self.operations)
        self.assertIn("data-ops-filter", self.operations)
        self.assertIn("data-ops-cards", self.operations)
        self.assertIn("data-staff-context-url", self.operations)
        self.assertIn('id="staff-context-drawer"', self.operations)

    def test_mobile_and_accessibility_guards(self):
        self.assertIn("@media(max-width:700px)", self.css)
        self.assertIn("@media(prefers-reduced-motion:reduce)", self.css)
        self.assertIn(":focus-visible", self.css)
        self.assertIn('font-family:var(--hub-font-body)', self.css)
        self.assertIn("IBM+Plex+Sans+Arabic", self.base)
        self.assertIn("Noto Sans Arabic", self.css)
