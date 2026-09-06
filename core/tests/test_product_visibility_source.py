from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from core.models import Category, Product


class ProductVisibilitySourceTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name_ar='اختبار')

    def test_ordinary_product_save_mirrors_canonical_fields_to_legacy_flags(self):
        product = Product.objects.create(
            category=self.category,
            name_ar='منتج عادي',
            price_syp=100,
            visible_on_qr=False,
            orderable_on_qr=True,
            product_type=Product.ProductType.FOOD,
            item_type=Product.ItemType.FOOD,
        )
        self.assertFalse(product.visible_on_pos)
        self.assertTrue(product.orderable_on_pos)

        product.visible_on_qr = True
        product.orderable_on_qr = False
        product.save()
        product.refresh_from_db()
        self.assertTrue(product.visible_on_pos)
        self.assertFalse(product.orderable_on_pos)

    def test_dedicated_internet_product_keeps_workflow_specific_legacy_flags(self):
        product = Product.objects.create(
            category=self.category,
            name_ar='هوية إنترنت',
            price_syp=100,
            visible_on_qr=True,
            orderable_on_qr=True,
            visible_on_pos=False,
            orderable_on_pos=False,
            product_type=Product.ProductType.INTERNET,
            item_type=Product.ItemType.SERVICE,
            service_type=Product.ServiceType.INTERNET,
        )
        self.assertFalse(product.visible_on_pos)
        self.assertFalse(product.orderable_on_pos)


class ProductVisibilityAdminContractTests(SimpleTestCase):
    def test_admin_hides_deprecated_pos_fields_and_labels_canonical_ones(self):
        root = Path(settings.BASE_DIR)
        base = (root / 'templates/admin/base_site.html').read_text(encoding='utf-8')
        css = (root / 'static/admin/css/product_visibility_source.css').read_text(encoding='utf-8')
        js = (root / 'static/admin/js/product_visibility_source.js').read_text(encoding='utf-8')

        self.assertIn("admin/css/product_visibility_source.css", base)
        self.assertIn("admin/js/product_visibility_source.js", base)
        self.assertIn('field-visible_on_pos', css)
        self.assertIn('field-orderable_on_pos', css)
        self.assertIn('Visible (Menu + POS)', js)
        self.assertIn('Orderable (Menu + POS)', js)
