"""Read-only contract for the first Hub Sweida 2026 visual implementation.

The design must remain a scoped presentation layer. It cannot alter catalog,
permissions, money calculations, Internet speed profiles or router operations.
"""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class HubVisual2026ContractTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        root = Path(settings.BASE_DIR)
        cls.base = (root / "templates/base.html").read_text(encoding="utf-8")
        cls.menu = (root / "templates/menu/menu.html").read_text(encoding="utf-8")
        cls.operations = (root / "templates/staff/home.html").read_text(encoding="utf-8")
        cls.css = (root / "static/css/hub_visual_2026.css").read_text(encoding="utf-8")

    def test_visual_layer_is_last_and_restricted_to_two_existing_surfaces(self):
        self.assertIn("{% static 'css/hub_visual_2026.css' %}", self.base)
        self.assertGreater(
            self.base.index("hub_visual_2026.css"),
            self.base.index("hub_ui_v3_suite.css"),
        )
        self.assertGreater(
            self.base.index("hub_visual_2026.css"),
            self.base.index("staff_ux5.css"),
        )
        selector = 'request.path|slice:":6" == "/menu/" or request.path == "/staff/"'
        # The existing v3 pilot uses the same two-route guard too.\n        self.assertGreaterEqual(self.base.count(selector), 2)
        self.assertIn("hub-visual-2026", self.base)
        self.assertIn("body.public-menu-page.hub-visual-2026", self.css)
        self.assertIn("body.staff-workspace-page.hub-visual-2026", self.css)
        self.assertNotIn("body.hub-provider-ui.hub-visual-2026", self.css)

    def test_palette_is_visual_not_operational(self):
        for color in ("#18b7a4", "#25c98d", "#f77732", "#a153d1",
                      "#ffc929", "#338ceb"):
            self.assertIn(color, self.css)
        self.assertIn("--hub26-action: #137f70;", self.css)
        self.assertNotIn("display:none!important", self.css)
        self.assertNotIn("pointer-events:none!important", self.css)
        self.assertIn(":focus-visible", self.css)
        self.assertIn("prefers-reduced-motion:reduce", self.css)
        self.assertIn("@media print", self.css)

    def test_existing_real_django_workflows_are_preserved(self):
        self.assertIn("system_settings.brand_logo_url", self.menu)
        self.assertIn('id="menu-order-form"', self.menu)
        self.assertIn('data-category-nav', self.menu)
        self.assertIn('data-submit-btn', self.menu)
        self.assertIn("section_products", self.menu)
        self.assertIn("data-ops-search", self.operations)
        self.assertIn("data-ops-cards", self.operations)
        self.assertIn("data-staff-context-url", self.operations)
        self.assertIn("staff_caps", self.operations)
        self.assertIn("staff/_context_drawer_shell.html", self.base)

    def test_high_contrast_customer_headings_on_decorative_surfaces(self):
        # WCAG 2.x normal-text contrast, tested for the intended bright
        # feature-card foreground/background pairs.
        def lum(value):
            rgb = [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [x / 12.92 if x <= 0.04045 else
                      ((x + 0.055) / 1.055) ** 2.4 for x in rgb]
            return sum(a * b for a, b in
                       zip(linear, (0.2126, 0.7152, 0.0722)))

        def contrast(a, b):
            values = sorted((lum(a), lum(b)), reverse=True)
            return (values[0] + .05) / (values[1] + .05)

        for background, foreground in (
            ("#18b7a4", "#073e3a"),
            ("#f77732", "#382319"),
            ("#137f70", "#ffffff"),
        ):
            self.assertGreaterEqual(contrast(background, foreground), 4.5)
