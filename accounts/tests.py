from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.forms import StaffUserEditForm
from accounts.models import StaffCapabilityOverride
from accounts.permissions import get_staff_capabilities, user_has_capability


class StaffCapabilityTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='cap-admin',
            password='pass',
            phone='+963700000001',
            role='admin',
        )
        self.cashier = User.objects.create_user(
            username='cap-cashier',
            password='pass',
            phone='+963700000002',
            role='cashier',
        )
        self.waiter = User.objects.create_user(
            username='cap-waiter',
            password='pass',
            phone='+963700000003',
            role='waiter',
        )
        self.kitchen = User.objects.create_user(
            username='cap-kitchen',
            password='pass',
            phone='+963700000004',
            role='kitchen',
        )

    def test_role_defaults_keep_preparation_for_station_operator_roles(self):
        self.assertTrue(user_has_capability(self.kitchen, 'kitchen_board'))
        self.assertTrue(user_has_capability(self.cashier, 'kitchen_board'))
        self.assertFalse(user_has_capability(self.waiter, 'kitchen_board'))
        self.assertTrue(user_has_capability(self.admin, 'kitchen_board'))

    def test_per_user_allow_and_deny_override_role_defaults(self):
        StaffCapabilityOverride.objects.create(
            user=self.waiter,
            capability='kitchen_board',
            allowed=True,
        )
        StaffCapabilityOverride.objects.create(
            user=self.cashier,
            capability='orders',
            allowed=False,
        )
        waiter = type(self.waiter).objects.get(pk=self.waiter.pk)
        cashier = type(self.cashier).objects.get(pk=self.cashier.pk)
        self.assertTrue(user_has_capability(waiter, 'kitchen_board'))
        self.assertFalse(user_has_capability(cashier, 'orders'))

    def test_admin_role_remains_full_access_even_if_stale_override_exists(self):
        StaffCapabilityOverride.objects.create(
            user=self.admin,
            capability='orders',
            allowed=False,
        )
        self.assertTrue(user_has_capability(self.admin, 'orders'))
        self.assertTrue(all(get_staff_capabilities(self.admin).values()))

    def test_staff_user_form_persists_and_removes_override(self):
        field_name = 'capability_kitchen_board'
        base_data = {
            'first_name': '',
            'last_name': '',
            'email': '',
            'phone': self.waiter.phone,
            'role': 'waiter',
            'is_active': 'on',
            'allow_django_admin_access': '',
        }
        form = StaffUserEditForm(
            data={**base_data, field_name: 'allow'},
            instance=self.waiter,
            actor=self.admin,
        )
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertTrue(user_has_capability(user, 'kitchen_board'))
        self.assertTrue(
            StaffCapabilityOverride.objects.filter(
                user=user,
                capability='kitchen_board',
                allowed=True,
            ).exists()
        )

        form = StaffUserEditForm(
            data={**base_data, field_name: 'inherit'},
            instance=user,
            actor=self.admin,
        )
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertFalse(user_has_capability(user, 'kitchen_board'))
        self.assertFalse(
            StaffCapabilityOverride.objects.filter(
                user=user,
                capability='kitchen_board',
            ).exists()
        )
