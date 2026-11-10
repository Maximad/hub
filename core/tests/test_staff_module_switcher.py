"""Role-aware module switcher: desktop and mobile navigation regression tests."""
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(ALLOWED_HOSTS=["testserver"], SECURE_SSL_REDIRECT=False)
class StaffModuleSwitcherTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.waiter = User.objects.create_user(
            username="module-waiter", phone="985778", password="unused", role="waiter"
        )
        cls.admin = User.objects.create_user(
            username="module-admin", phone="985779", password="unused", role="admin",
            is_staff=True,
        )

    def test_staff_home_and_pos_have_same_two_native_module_menus(self):
        self.client.force_login(self.waiter)
        for route in ("staff_home", "staff_pos"):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'data-staff-module-menu', count=2)
                self.assertContains(response, 'class="staff-module-menu__panel"', count=2)
                self.assertContains(response, 'فتح دليل الأقسام')
                self.assertContains(response, reverse("staff_operational_tasks"))
                self.assertContains(response, reverse("staff_home") + "#staff-more")
                self.assertNotContains(response, reverse("staff_users_list"))

    def test_admin_sees_cross_module_navigation(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("staff_home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'البرنامج والجمهور')
        self.assertContains(response, 'الإدارة والموارد')
        self.assertContains(response, reverse("staff_events"))
        self.assertContains(response, reverse("staff_reservations"))
        self.assertContains(response, reverse("staff_inventory_home"))
        self.assertContains(response, reverse("staff_finance_home"))

    def test_styles_and_escape_close_are_progressive(self):
        root = Path(settings.BASE_DIR)
        source = (root / "templates/includes/staff_shell_nav.html").read_text(encoding="utf-8")
        shared = (root / "templates/includes/staff_module_links.html").read_text(encoding="utf-8")
        css = (root / "static/css/staff_account_menu.css").read_text(encoding="utf-8")
        js = (root / "static/js/staff_account_menu.js").read_text(encoding="utf-8")
        self.assertEqual(source.count('{% include "includes/staff_module_links.html" %}'), 2)
        for marker in ("staff_caps.events", "staff_caps.reservations", "staff_caps.inventory",
                       "staff_caps.finance", "staff_caps.users"):
            self.assertIn(marker, shared)
        self.assertIn("staff-mobile-nav .staff-module-menu--mobile", css)
        self.assertIn(":focus-visible", css)
        self.assertIn("details[data-staff-module-menu]", js)
        self.assertIn("Escape", js)
        self.assertIn("!menu.contains(event.target)", js)
