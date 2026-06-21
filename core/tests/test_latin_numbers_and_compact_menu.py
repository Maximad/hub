from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from catalog.models import MenuSection, ProductOption, ProductOptionGroup, ProductOptionGroupAssignment
from core.models import Category, Product, SystemSetting
from core.settings_helpers import get_system_settings
from core.templatetags.hub_numbers import format_syp, latin_digits


class LatinNumberFormattingTests(TestCase):
    def test_format_syp_uses_latin_digits_and_separators(self):
        self.assertEqual(format_syp(25000), '25,000 ل.س')

    def test_latin_digits_converts_arabic_indic_input(self):
        self.assertEqual(latin_digits('٢٥,٠٠٠ ل.س'), '25,000 ل.س')

    def test_latin_digits_converts_persian_input(self):
        self.assertEqual(latin_digits('۲۵,۰۰۰ ل.س'), '25,000 ل.س')

    def test_blank_values_do_not_crash(self):
        self.assertEqual(latin_digits(None), '')
        self.assertEqual(format_syp(''), '')


class CompactMenuRenderingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='numbers-admin', password='pass', email='numbers@example.com', phone='+963900000003'
        )
        self.category = Category.objects.create(name_ar='مشروبات')
        self.section = MenuSection.objects.create(name_ar='القهوة')
        self.product = Product.objects.create(
            category=self.category,
            name_ar='لاتيه',
            description_ar='وصف قصير للقهوة',
            price_syp=25000,
            visible_on_qr=True,
            orderable_on_qr=True,
        )
        self.product.menu_sections.add(self.section)
        self.group = ProductOptionGroup.objects.create(code='size', name_ar='الحجم', is_required=True, min_selected=1)
        ProductOption.objects.create(group=self.group, code='large', name_ar='كبير', price_delta_syp=5000)
        ProductOptionGroupAssignment.objects.create(product=self.product, group=self.group)

    def tearDown(self):
        get_system_settings.cache_clear()

    def _settings(self, **kwargs):
        get_system_settings.cache_clear()
        setting = SystemSetting.objects.create(system_title_ar='اختبار الأرقام', **kwargs)
        get_system_settings.cache_clear()
        return setting

    @override_settings(DEBUG_PROPAGATE_EXCEPTIONS=True, ALLOWED_HOSTS=['testserver'])
    def test_public_menu_compact_list_uses_clickable_rows_and_modal_controls(self):
        self._settings(public_menu_layout=SystemSetting.PublicMenuLayout.COMPACT_LIST, show_item_notes=True)
        response = self.client.get('/menu/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'menu-layout-compact_list')
        self.assertContains(response, '25,000 ل.س')
        self.assertNotContains(response, '٢٥')
        self.assertContains(response, 'data-menu-item-open')
        self.assertContains(response, 'role="button"')
        self.assertContains(response, 'data-menu-modal')
        self.assertContains(response, 'menu-item-modal-source')
        self.assertContains(response, 'name="note_')
        self.assertContains(response, 'خيارات متاحة')
        self.assertContains(response, 'menu-product-media--placeholder')
        self.assertNotContains(response, 'menu-list-actions')

    @override_settings(DEBUG_PROPAGATE_EXCEPTIONS=True, ALLOWED_HOSTS=['testserver'])
    def test_invalid_public_menu_layout_falls_back(self):
        setting = self._settings()
        SystemSetting.objects.filter(pk=setting.pk).update(public_menu_layout='bad')
        get_system_settings.cache_clear()
        response = self.client.get('/menu/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'public-menu-layout-comfortable')

    @override_settings(DEBUG_PROPAGATE_EXCEPTIONS=True, ALLOWED_HOSTS=['testserver'])
    def test_staff_pos_contains_latin_digit_prices_and_core_staff_pages_load(self):
        self.client.force_login(self.user)
        for path in ['/staff/pos/', '/staff/orders/', '/staff/cashier/']:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertLess(response.status_code, 500)
        response = self.client.get('/staff/pos/')
        self.assertContains(response, '25,000 ل.س')
