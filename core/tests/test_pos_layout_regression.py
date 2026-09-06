from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class PosLayoutRegressionTests(SimpleTestCase):
    def test_pos_wrapper_neutralizes_catalog_grid_classes(self):
        css = (Path(settings.BASE_DIR) / 'static/css/staff_pos_v2.css').read_text(encoding='utf-8')
        self.assertIn(
            '.hub-staff-ui .staff-pos{display:grid;grid-template-columns:1fr!important;gap:12px}',
            css,
        )

    def test_product_grid_keeps_desktop_multi_column_layout(self):
        css = (Path(settings.BASE_DIR) / 'static/css/staff_pos_v2.css').read_text(encoding='utf-8')
        self.assertIn(
            '.hub-staff-ui .staff-pos__grid{display:grid!important;grid-template-columns:repeat(4,minmax(0,1fr))!important;',
            css,
        )
