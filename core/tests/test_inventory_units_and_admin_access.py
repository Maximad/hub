"""Stock count measurement labels and superuser-only Django admin regression."""
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.middleware import VIEW_AS_USER_SESSION_KEY
from core.models import InventoryItem, StockMovement


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class InventoryCountUnitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.cashier = User.objects.create_user(
            username='unit-counter', phone='983111', password='PasswordForTests-2030', role='cashier'
        )
        cls.weight = InventoryItem.objects.create(
            name_ar='بن', unit=InventoryItem.Unit.KG, current_quantity=Decimal('2.500'),
        )
        cls.volume = InventoryItem.objects.create(
            name_ar='حليب', unit=InventoryItem.Unit.ML, current_quantity=Decimal('750.000'),
        )
        cls.pieces = InventoryItem.objects.create(
            name_ar='أكواب ورقية', unit=InventoryItem.Unit.PIECE, current_quantity=Decimal('40.000'),
        )

    def test_count_entry_shows_units_adjacent_to_quantities(self):
        self.client.force_login(self.cashier)
        for item, displayed in ((self.weight, 'كغ'), (self.volume, 'مل'), (self.pieces, 'قطعة')):
            with self.subTest(unit=displayed):
                response = self.client.get(reverse('staff_inventory_stock_count'), {'item': item.pk})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'id="stock-count-unit-hint"')
                self.assertContains(response, f'وحدة القياس: <strong>{displayed}</strong>', html=False)
                self.assertContains(response, f'data-count-unit="{displayed}"')
                self.assertContains(response, f'data-recorded="{item.current_quantity}"')
                self.assertContains(response, 'aria-describedby="stock-count-unit-hint"')
                self.assertEqual(response.context['item'].current_quantity, item.current_quantity)
                self.assertEqual(response.context['item'].get_unit_display(), displayed)
                self.assertEqual(StockMovement.objects.count(), 0)

    def test_preview_still_preserves_unit_and_registered_quantity(self):
        self.client.force_login(self.cashier)
        response = self.client.post(reverse('staff_inventory_stock_count'), {
            'action': 'preview', 'item': str(self.weight.pk), 'count': '2.250', 'reason': 'فرق جرد',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['preview']['after'], Decimal('2.250'))
        self.assertEqual(response.context['preview']['before'], Decimal('2.500'))
        self.assertEqual(response.context['preview']['unit'], 'كغ')
        self.assertContains(response, 'الكمية السابقة')
        self.assertContains(response, 'الكمية التي وجدتها')
        self.assertEqual(StockMovement.objects.count(), 0)


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class SuperuserAdminAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.superuser = User.objects.create_superuser(
            username='root-admin', phone='983112', password='LongTestPassword#901'
        )
        cls.manager = User.objects.create_user(
            username='not-superuser-manager', phone='983113', password='LongTestPassword#901',
            role='admin', is_staff=True,
        )
        cls.cashier = User.objects.create_user(
            username='not-superuser-cashier', phone='983114', password='LongTestPassword#901',
            role='cashier', is_staff=True,
        )

    def test_admin_site_requires_superuser_not_staff_or_hub_manager_role(self):
        from config.admin_site import SuperuserAdminSite, SuperuserAdminAuthenticationForm
        self.assertIsInstance(admin.site, SuperuserAdminSite)
        self.assertIs(admin.site.login_form, SuperuserAdminAuthenticationForm)
        for user in (self.manager, self.cashier):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                for route in ('admin:index', 'admin:core_inventoryitem_changelist', 'admin:password_change'):
                    response = self.client.get(reverse(route))
                    self.assertNotEqual(response.status_code, 200)
                staff = self.client.get(reverse('staff_home'))
                self.assertEqual(staff.status_code, 200)
                self.assertNotContains(staff, 'href="%s"' % reverse('admin:index'))
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(reverse('admin:index')).status_code, 200)
        self.assertEqual(self.client.get(reverse('admin:core_inventoryitem_changelist')).status_code, 200)
        self.assertContains(self.client.get(reverse('staff_home')), 'href="%s"' % reverse('admin:index'))

    def test_non_superuser_cannot_authenticate_through_admin_login(self):
        self.client.logout()
        response = self.client.post(reverse('admin:login'), {
            'username': self.manager.username, 'password': 'LongTestPassword#901',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)
        self.assertNotEqual(self.client.get(reverse('admin:index')).status_code, 200)

    def test_inactive_superuser_cannot_use_admin(self):
        self.superuser.is_active = False
        self.superuser.save(update_fields=['is_active'])
        self.client.force_login(self.superuser)
        self.assertNotEqual(self.client.get(reverse('admin:index')).status_code, 200)

    def test_view_as_hides_advanced_admin_but_remains_read_only(self):
        self.client.force_login(self.superuser)
        session = self.client.session
        session[VIEW_AS_USER_SESSION_KEY] = self.manager.pk
        session.save()
        preview = self.client.get(reverse('staff_home'))
        self.assertEqual(preview.status_code, 200)
        self.assertNotContains(preview, 'href="%s"' % reverse('admin:index'))
        self.assertEqual(self.client.get(reverse('admin:index')).status_code, 200)

    def test_staff_login_is_independent_of_admin_and_preserves_safe_staff_destination(self):
        self.client.logout()
        target = reverse('staff_home')
        url = reverse('wifi_staff_login') + '?next=' + target
        response = self.client.get(target)
        self.assertRedirects(response, url, fetch_redirect_response=False)
        login_page = self.client.get(url)
        self.assertContains(login_page, 'name="next" value="%s"' % target)
        logged_in = self.client.post(reverse('wifi_staff_login'), {
            'username': self.manager.username, 'password': 'LongTestPassword#901', 'next': target,
        })
        self.assertRedirects(logged_in, target)
        self.assertEqual(self.client.get(target).status_code, 200)

    def test_staff_login_does_not_redirect_to_external_or_admin_destination(self):
        self.client.logout()
        for target in ('//evil.example/path', 'https://evil.example/', '/admin/', '/\\evil.example/'):
            response = self.client.get(reverse('wifi_staff_login'), {'next': target})
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'name="next"')
