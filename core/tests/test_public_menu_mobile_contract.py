from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class PublicMenuMobileContractTests(SimpleTestCase):
    def _read(self, relative_path):
        return Path(settings.BASE_DIR, relative_path).read_text(encoding='utf-8')

    def test_public_menu_flow_styles_are_loaded_after_shared_menu_styles(self):
        base = self._read('templates/base.html')
        self.assertIn("{% static 'css/public_menu_flow.css' %}", base)
        self.assertGreater(
            base.index("{% static 'css/public_menu_flow.css' %}"),
            base.index("{% static 'css/hub.css' %}"),
        )

    def test_mobile_cart_uses_one_scroll_body_and_separate_submit_footer(self):
        css = self._read('static/css/public_menu_flow.css')
        javascript = self._read('static/js/menu_cart.js')

        self.assertIn('.public-menu-cart-submit-footer', css)
        self.assertIn('.public-menu-cart-scroll', css)
        self.assertIn('overflow-y:auto!important', css)
        self.assertIn('100dvh', css)
        self.assertIn('env(safe-area-inset-bottom)', css)
        self.assertIn('function stabilizeCartLayout()', javascript)
        self.assertIn("footer.className = 'public-menu-cart-submit-footer'", javascript)
        self.assertIn('footer.appendChild(submitWrap)', javascript)

    def test_new_product_modal_uses_quantity_one_as_uncommitted_draft(self):
        javascript = self._read('static/js/menu_cart.js')

        self.assertIn('activeModalInitialQuantity', javascript)
        self.assertIn('if (qtyInput && activeModalInitialQuantity < 1) qtyInput.value = 1;', javascript)
        self.assertIn("if (qtyInput) qtyInput.value = 0;", javascript)
        self.assertIn('activeModalCommitted = true;', javascript)
