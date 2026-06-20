from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from catalog.models import MediaAsset
from core.models import SystemSetting
from core.settings_helpers import get_system_settings


class BrandingMediaSafetyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='branding-admin', password='pass', email='branding@example.com', phone='+963900000001'
        )
        self.client.force_login(self.user)

    def tearDown(self):
        get_system_settings.cache_clear()

    def _settings(self, **kwargs):
        get_system_settings.cache_clear()
        setting = SystemSetting.objects.create(system_title_ar='اختبار العلامة', **kwargs)
        get_system_settings.cache_clear()
        return setting

    def _assert_loads(self, path):
        response = self.client.get(path)
        self.assertLess(response.status_code, 500, path)
        return response

    @override_settings(DEBUG_PROPAGATE_EXCEPTIONS=True, ALLOWED_HOSTS=['testserver'])
    def test_menu_loads_with_no_branding_media(self):
        self._settings()
        self._assert_loads('/menu/')

    @override_settings(DEBUG_PROPAGATE_EXCEPTIONS=True, ALLOWED_HOSTS=['testserver'])
    def test_menu_loads_with_uploaded_logo_and_banner(self):
        with TemporaryDirectory() as tmp:
            with override_settings(MEDIA_ROOT=tmp):
                logo = MediaAsset.objects.create(title_ar='شعار', file=SimpleUploadedFile('logo.png', b'logo', content_type='image/png'))
                banner = MediaAsset.objects.create(title_ar='بانر', file=SimpleUploadedFile('banner.png', b'banner', content_type='image/png'))
                self._settings(brand_logo_media=logo, public_menu_banner_media=banner)
                response = self._assert_loads('/menu/')
                self.assertContains(response, logo.safe_url)
                self.assertContains(response, banner.safe_url)

    @override_settings(DEBUG_PROPAGATE_EXCEPTIONS=True, ALLOWED_HOSTS=['testserver'])
    def test_menu_loads_with_empty_media_asset(self):
        media = MediaAsset.objects.create(title_ar='فارغ')
        self._settings(brand_logo_media=media, public_menu_banner_media=media)
        response = self._assert_loads('/menu/')
        self.assertEqual(media.safe_url, '')
        self.assertNotContains(response, 'menu-public__logo')

    @override_settings(DEBUG_PROPAGATE_EXCEPTIONS=True, ALLOWED_HOSTS=['testserver'])
    def test_staff_pos_loads_with_pos_logo_set(self):
        media = MediaAsset.objects.create(title_ar='رابط POS', external_url='https://example.com/pos.png')
        self._settings(pos_logo_media=media)
        self._assert_loads('/staff/pos/')

    @override_settings(DEBUG_PROPAGATE_EXCEPTIONS=True, ALLOWED_HOSTS=['testserver'])
    def test_staff_orders_and_cashier_load_with_branding_media_set(self):
        media = MediaAsset.objects.create(title_ar='رابط شعار', external_url='https://example.com/logo.png')
        self._settings(brand_icon_media=media, brand_logo_media=media)
        self._assert_loads('/staff/orders/')
        self._assert_loads('/staff/cashier/')

    @override_settings(DEBUG_PROPAGATE_EXCEPTIONS=True, ALLOWED_HOSTS=['testserver'])
    def test_admin_system_settings_page_loads_with_branding_media(self):
        media = MediaAsset.objects.create(title_ar='خارجي', external_url='https://example.com/logo.png')
        self._settings(brand_logo_media=media, public_menu_banner_media=media, pos_logo_media=media, receipt_logo_media=media)
        self._assert_loads('/admin/core/systemsetting/')

    @override_settings(DEBUG_PROPAGATE_EXCEPTIONS=True, ALLOWED_HOSTS=['testserver'])
    def test_invalid_or_missing_branding_media_does_not_500(self):
        missing = MediaAsset.objects.create(title_ar='مفقود', file='products/missing.png')
        invalid = MediaAsset.objects.create(title_ar='غير صالح', external_url='javascript:alert(1)')
        self._settings(brand_logo_media=missing, brand_icon_media=invalid, public_menu_banner_media=missing, pos_logo_media=missing)
        self.assertEqual(missing.safe_url, '')
        self.assertEqual(invalid.safe_url, '')
        for path in ['/menu/', '/staff/pos/', '/staff/orders/', '/staff/cashier/', '/admin/core/systemsetting/']:
            self._assert_loads(path)
