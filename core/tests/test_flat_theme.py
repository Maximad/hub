from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.template import Context, Template
from django.test import RequestFactory, TestCase, override_settings

from core.admin import SystemSettingAdmin, SystemSettingAdminForm
from core.context_processors import system_settings
from core.models import SystemSetting
from core.settings_helpers import get_system_settings


class FlatThemeSafetyTests(TestCase):
    def tearDown(self):
        get_system_settings.cache_clear()

    def test_default_theme_does_not_create_a_settings_record(self):
        request = RequestFactory().get('/')
        request.user = get_user_model()()
        context = system_settings(request)
        self.assertEqual(SystemSetting.objects.count(), 0)
        self.assertEqual(context['system_settings'].safe_primary_color, '#0f5f57')

    def test_valid_theme_values_save(self):
        setting = SystemSetting(primary_color='#123abc', control_height_px=48)
        setting.full_clean()

    def test_invalid_and_unsafe_colors_are_rejected(self):
        for value in ('red;}</style><script>', 'url(https://bad)', '#12zzzz'):
            setting = SystemSetting(primary_color=value)
            with self.assertRaises(ValidationError):
                setting.full_clean()

    def test_numeric_theme_ranges_are_validated(self):
        form = SystemSettingAdminForm(data={'control_height_px': 10})
        self.assertFalse(form.is_valid())
        self.assertIn('control_height_px', form.errors)

    def test_theme_partial_contains_semantic_tokens(self):
        setting = SystemSetting(primary_color='#123abc')
        html = Template('{% include "includes/hub_theme.html" %}').render(Context({
            'system_settings': setting, 'hub_theme_numbers': setting.safe_theme_numbers,
        }))
        self.assertIn('--hub-color-primary:#123abc', html)
        self.assertIn('--hub-color-danger:', html)

    def test_icons_are_allowlisted_and_can_be_disabled(self):
        allowed = Template('{% load hub_icons %}{% hub_icon "search" %}').render(Context({'hub_icons_enabled': True}))
        unknown = Template('{% load hub_icons %}{% hub_icon request_name %}').render(Context({'hub_icons_enabled': True, 'request_name': '<svg onload=alert(1)>'}))
        disabled = Template('{% load hub_icons %}{% hub_icon "search" %}بحث').render(Context({'hub_icons_enabled': False}))
        self.assertIn('<svg', allowed)
        self.assertEqual(unknown, '')
        self.assertEqual(disabled, 'بحث')

    def test_theme_cache_is_invalidated_after_save(self):
        setting = SystemSetting.objects.create(primary_color='#123abc')
        self.assertEqual(get_system_settings().safe_primary_color, '#123abc')
        setting.primary_color = '#abcdef'
        setting.save()
        self.assertEqual(get_system_settings().safe_primary_color, '#abcdef')


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class FlatThemeAdminTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username='theme-admin', password='pass', phone='+963900100001')
        self.regular = get_user_model().objects.create_user(username='theme-user', password='pass', phone='+963900100002')
        self.setting = SystemSetting.objects.create()

    def test_authorized_admin_can_access_settings(self):
        self.client.force_login(self.admin)
        response = self.client.get(f'/admin/core/systemsetting/{self.setting.pk}/change/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'الواجهة والتصميم')
        self.assertContains(response, 'hub-theme-tokens')

    def test_unauthorized_user_cannot_change_settings(self):
        request = RequestFactory().post('/')
        request.user = self.regular
        model_admin = SystemSettingAdmin(SystemSetting, AdminSite())
        self.assertFalse(model_admin.has_change_permission(request, self.setting))
