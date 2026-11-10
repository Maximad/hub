"""POS charcoal design: source-level invariants, no live ordering side effects."""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class StaffCharcoalPosContractTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        root = Path(settings.BASE_DIR)
        cls.pos = (root / "templates/staff/pos.html").read_text(encoding="utf-8")
        cls.base = (root / "templates/base.html").read_text(encoding="utf-8")
        cls.css = (root / "static/css/staff_charcoal.css").read_text(encoding="utf-8")

    def test_real_pos_is_explicitly_opted_into_charcoal(self):
        self.assertIn('{% block body_class %}staff-charcoal-pos{% endblock %}', self.pos)
        self.assertIn("body.hub-staff-ui.hub-ui-v3.staff-charcoal-pos {", self.css)
        self.assertIn("{% static 'css/staff_charcoal.css' %}", self.base)
        self.assertIn("body.staff-workspace-page.hub-staff-ui.hub-ui-v3 .ops-designed", self.css)

    def test_ordering_flow_and_role_boundaries_stay_intact(self):
        for value in (
            'id="menu-order-form"', '{% csrf_token %}', 'name="fulfillment_mode"',
            "staff-pos__catalog", "staff-pos__cart", "staff-pos__submit",
            "menu_cart.js", "hub_cart_common.js", "menu-product-card",
        ):
            self.assertIn(value, self.pos)
        self.assertNotIn("form.staff-pos__form{", self.css)
        self.assertNotIn("pointer-events:none!important", self.css)

    def test_touch_accessibility_and_low_motion(self):
        self.assertIn("staff-charcoal-pos :is(a,button,input,select,textarea,summary):focus-visible", self.css)
        self.assertIn("staff-charcoal-pos .staff-pos__cart", self.css)
        self.assertIn("@media(max-width:700px)", self.css)
        self.assertIn("@media(prefers-reduced-motion:reduce)", self.css)
        self.assertIn("color-scheme:dark", self.css)
        self.assertIn("@media print", self.css)
