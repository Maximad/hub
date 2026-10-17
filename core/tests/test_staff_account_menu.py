"""Staff header account menu and safe self-service actions."""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from accounts.middleware import VIEW_AS_USER_SESSION_KEY


@override_settings(ALLOWED_HOSTS=["testserver"], SECURE_SSL_REDIRECT=False)
class StaffAccountMenuTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_user(
            username="menu-admin", phone="985741", password="OldTestPassword#302", role="admin",
            is_staff=True,
        )
        cls.waiter = User.objects.create_user(
            username="menu-waiter", phone="985742", password="OldTestPassword#302", role="waiter",
        )
        cls.superuser = User.objects.create_superuser(
            username="menu-root", phone="985743", password="OldTestPassword#302",
        )

    def test_account_menu_is_available_on_every_staff_page_and_uses_post_for_logout(self):
        self.client.force_login(self.waiter)
        for name in ("staff_home", "staff_pos"):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'data-staff-account-menu')
                self.assertContains(response, reverse("staff_profile"))
                self.assertContains(response, reverse("staff_notifications_preferences"))
                self.assertNotContains(response, reverse("staff_password_change"))
                self.assertContains(response, 'method="post" action="%s"' % reverse("staff_logout"))
                self.assertContains(response, 'staff_account_menu.css')
                self.assertContains(response, 'staff_account_menu.js')
                self.assertNotContains(response, reverse("staff_users_list"))
                self.assertNotContains(response, reverse("staff_internet_settings"))

    def test_admin_gets_management_links_and_not_in_the_operations_directory(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("staff_home"))
        self.assertEqual(response.status_code, 200)
        for route in ("staff_users_list", "staff_menu_tools", "staff_qr_links", "staff_reports_home"):
            self.assertContains(response, reverse(route))
        self.assertContains(response, 'aria-label="الإدارة والإعدادات"')
        html = response.content.decode()
        directory = html.split('id="staff-more"', 1)[1].split('id="staff-help"', 1)[0]
        self.assertNotIn("المستخدمون والصلاحيات", directory)
        self.assertNotIn("إدارة القائمة", directory)

    def test_staff_can_read_only_own_profile_not_administrative_user_detail(self):
        self.client.force_login(self.waiter)
        response = self.client.get(reverse("staff_profile"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "menu-waiter")
        self.assertNotContains(response, "menu-admin")
        self.assertContains(response, reverse("staff_password_change"))
        restricted = self.client.get(reverse("staff_user_detail", kwargs={"user_id": self.admin.pk}))
        self.assertNotEqual(restricted.status_code, 200)

    def test_staff_can_change_only_their_own_password_and_keep_session(self):
        self.client.force_login(self.waiter)
        response = self.client.post(reverse("staff_password_change"), {
            "old_password": "OldTestPassword#302",
            "new_password1": "NewTestPassword#303",
            "new_password2": "NewTestPassword#303",
        })
        self.assertRedirects(response, reverse("staff_profile"))
        self.waiter.refresh_from_db()
        self.admin.refresh_from_db()
        self.assertTrue(self.waiter.check_password("NewTestPassword#303"))
        self.assertTrue(self.admin.check_password("OldTestPassword#302"))
        self.assertEqual(self.client.get(reverse("staff_profile")).status_code, 200)

    def test_wrong_existing_password_is_rejected(self):
        self.client.force_login(self.waiter)
        response = self.client.post(reverse("staff_password_change"), {
            "old_password": "incorrect",
            "new_password1": "NewTestPassword#303",
            "new_password2": "NewTestPassword#303",
        })
        self.assertEqual(response.status_code, 200)
        self.waiter.refresh_from_db()
        self.assertTrue(self.waiter.check_password("OldTestPassword#302"))

    def test_logout_requires_post_and_csrf_and_ends_session(self):
        self.client.force_login(self.waiter)
        self.assertEqual(self.client.get(reverse("staff_logout")).status_code, 405)
        self.assertRedirects(self.client.post(reverse("staff_logout")), reverse("wifi_staff_login"))
        self.assertNotEqual(self.client.get(reverse("staff_profile")).status_code, 200)

        checked = Client(enforce_csrf_checks=True)
        checked.force_login(self.admin)
        denied = checked.post(reverse("staff_logout"))
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(checked.get(reverse("staff_profile")).status_code, 200)

    def test_view_as_cannot_change_password_or_logout(self):
        self.client.force_login(self.superuser)
        session = self.client.session
        session[VIEW_AS_USER_SESSION_KEY] = self.waiter.pk
        session.save()
        preview = self.client.get(reverse("staff_profile"))
        self.assertEqual(preview.status_code, 200)
        self.assertNotContains(preview, reverse("staff_password_change"))
        self.assertNotContains(preview, 'action="%s"' % reverse("staff_logout"))
        self.assertEqual(self.client.post(reverse("staff_password_change"), {}).status_code, 403)
        self.assertEqual(self.client.post(reverse("staff_logout"), {}).status_code, 403)

    def test_anonymous_user_cannot_view_account(self):
        self.assertNotEqual(self.client.get(reverse("staff_profile")).status_code, 200)
