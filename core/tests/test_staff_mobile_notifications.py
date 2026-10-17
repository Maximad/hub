"""Regression checks for staff account and mobile notification UI."""
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.middleware import VIEW_AS_USER_SESSION_KEY
from core.models import NotificationPreference


@override_settings(ALLOWED_HOSTS=["testserver"], SECURE_SSL_REDIRECT=False)
class StaffMobileNotificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.staff = User.objects.create_user(
            username="notification-staff", phone="984011", password="safe-long-password-X9", role="waiter"
        )
        cls.other = User.objects.create_user(
            username="notification-other", phone="984012", password="safe-long-password-X9", role="waiter"
        )
        cls.admin = User.objects.create_superuser(
            username="notification-admin", phone="984013", password="safe-long-password-X9"
        )

    def test_menu_has_notification_settings_but_not_password_change(self):
        self.client.force_login(self.staff)
        for route in ("staff_home", "staff_pos"):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'href="%s">إعدادات التنبيهات</a>' % reverse("staff_notifications_preferences"))
                self.assertNotContains(response, 'href="%s">تغيير كلمة المرور</a>' % reverse("staff_password_change"))

        profile = self.client.get(reverse("staff_profile"))
        self.assertContains(profile, reverse("staff_password_change"))

    def test_settings_get_is_read_only_and_existing_post_contract_is_preserved(self):
        self.client.force_login(self.staff)
        url = reverse("staff_notifications_preferences")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "إعدادات التنبيهات")
        self.assertContains(response, 'id="staff-notification-settings-sound"')
        self.assertContains(response, 'id="staff-notification-settings-browser"')
        self.assertEqual(NotificationPreference.objects.filter(user=self.staff).count(), 0)
        response = self.client.post(url, {"enable_sound": "0", "enable_browser_notifications": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ok"], True)
        self.assertFalse(NotificationPreference.objects.get(user=self.staff).enable_sound)
        self.assertTrue(NotificationPreference.objects.get(user=self.staff).enable_browser_notifications)
        self.assertFalse(NotificationPreference.objects.filter(user=self.other).exists())

    def test_notifications_preferences_post_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        response = client.post(reverse("staff_notifications_preferences"), {"enable_sound": "0"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(NotificationPreference.objects.filter(user=self.staff).exists())

    def test_view_as_settings_cannot_write_and_get_does_not_create_preferences(self):
        self.client.force_login(self.admin)
        session = self.client.session
        session[VIEW_AS_USER_SESSION_KEY] = self.staff.pk
        session.save()
        response = self.client.get(reverse("staff_notifications_preferences"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "للقراءة فقط")
        self.assertNotContains(response, '<script src="/static/js/staff_notifications.js"></script>')
        self.assertFalse(NotificationPreference.objects.filter(user=self.staff).exists())
        self.assertEqual(self.client.post(reverse("staff_notifications_preferences"), {"enable_sound": "0"}).status_code, 403)

    def test_password_hints_are_arabic_and_fit_readable_form(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("staff_password_change"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "اختر كلمة مرور طويلة وفريدة")
        self.assertNotContains(response, "Your password")
        stylesheet = (Path(settings.BASE_DIR) / "static/css/staff_account_menu.css").read_text(encoding="utf-8")
        self.assertIn("grid-template-columns: minmax(0, 1fr)", stylesheet)
        self.assertIn("overflow-wrap: anywhere", stylesheet)

    def test_shared_template_comment_is_not_visible_on_staff_pages(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("staff_pos"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Shared contextual workspace")
        self.assertNotContains(response, "server-side permission checks remain authoritative")
        source = (Path(settings.BASE_DIR) / "templates/staff/_context_drawer_shell.html").read_text(encoding="utf-8")
        self.assertIn("{% comment %}", source)
        self.assertIn("{% endcomment %}", source)

    def test_mobile_notification_popover_is_anchored_to_viewport(self):
        stylesheet = (Path(settings.BASE_DIR) / "static/css/staff_account_menu.css").read_text(encoding="utf-8")
        javascript = (Path(settings.BASE_DIR) / "static/js/staff_notifications.js").read_text(encoding="utf-8")
        self.assertIn(".staff-app-header__notifications .staff-notification-dropdown", stylesheet)
        self.assertIn("position: fixed;", stylesheet)
        self.assertIn("inset-inline-start: max(12px", stylesheet)
        self.assertIn("inset-inline-end: max(12px", stylesheet)
        self.assertIn("soundSettingsBtn.addEventListener", javascript)
        self.assertIn("browserSettingsBtn.addEventListener", javascript)
