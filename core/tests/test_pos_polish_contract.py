from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class PosPolishContractTests(SimpleTestCase):
    def setUp(self):
        self.root = Path(settings.BASE_DIR)
        self.base = (self.root / 'templates/base.html').read_text(encoding='utf-8')
        self.css = (self.root / 'static/css/staff_pos_polish.css').read_text(encoding='utf-8')
        self.js = (self.root / 'static/js/staff_pos_polish.js').read_text(encoding='utf-8')

    def test_pos_polish_assets_are_loaded_after_existing_pos_layer(self):
        pos_css = "{% static 'css/staff_pos_v2.css' %}"
        polish_css = "{% static 'css/staff_pos_polish.css' %}"
        polish_js = "{% static 'js/staff_pos_polish.js' %}"
        self.assertIn(polish_css, self.base)
        self.assertGreater(self.base.index(polish_css), self.base.index(pos_css))
        self.assertIn(polish_js, self.base)

    def test_fulfillment_radios_are_progressively_enhanced_to_compact_select(self):
        self.assertIn("[data-fulfillment-selector]", self.js)
        self.assertIn("staff-pos__fulfillment-select", self.js)
        self.assertIn("target.checked = true", self.js)
        self.assertIn("new Event('change', { bubbles: true })", self.js)
        self.assertIn("selectMode('table', false)", self.js)

    def test_visit_bound_pos_hides_redundant_context_controls(self):
        self.assertIn("contextBoundAtLoad = Boolean(visitSelect?.value)", self.js)
        self.assertIn("fulfillmentField.hidden = true", self.js)
        self.assertIn("tableField.hidden = true", self.js)
        self.assertIn("visitField.hidden = true", self.js)
        self.assertIn("staff-pos__bound-context", self.js)
        self.assertIn("الطلب مرتبط بالحساب الحالي", self.js)

    def test_general_pos_defaults_to_table_and_non_table_modes_clear_table(self):
        self.assertIn("selectMode('table', false)", self.js)
        self.assertIn("tableSelect.disabled = !tableMode", self.js)
        self.assertIn("if (!tableMode) tableSelect.value = ''", self.js)

    def test_pos_radio_fallback_and_filters_are_compact(self):
        self.assertIn('.hub-staff-ui .staff-pos input[type="radio"]', self.css)
        self.assertIn('.hub-staff-ui .staff-pos [hidden]{display:none!important}', self.css)
        self.assertIn('width:min(100%,820px)', self.css)
        self.assertIn('max-width:230px', self.css)
        self.assertIn('max-width:560px', self.css)
        self.assertIn('.hub-staff-ui .staff-pos__bound-context{', self.css)
