from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.forms import StaffUserEditForm
from accounts.models import UserCapabilityOverride
from accounts.permissions import get_staff_capabilities, user_has_capability


class StaffCapabilityTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='cap-admin', password='pass', phone='+963100000001', role='admin'
        )
        self.waiter = User.objects.create_user(
            username='cap-waiter', password='pass', phone='+963100000002', role='waiter'
        )
        self.kitchen = User.objects.create_user(
            username='cap-kitchen', password='pass', phone='+963100000003', role='kitchen'
        )

    def test_role_defaults_keep_kitchen_board_to_admin_and_kitchen(self):
        self.assertTrue(user_has_capability(self.admin, 'kitchen_board'))
        self.assertTrue(user_has_capability(self.kitchen, 'kitchen_board'))
        self.assertFalse(user_has_capability(self.waiter, 'kitchen_board'))

    def test_per_user_allow_override_grants_capability(self):
        UserCapabilityOverride.objects.create(
            user=self.waiter,
            capability='kitchen_board',
            allowed=True,
        )
        self.assertTrue(user_has_capability(self.waiter, 'kitchen_board'))

    def test_per_user_deny_override_removes_role_capability(self):
        UserCapabilityOverride.objects.create(
            user=self.kitchen,
            capability='inventory',
            allowed=False,
        )
        self.assertFalse(user_has_capability(self.kitchen, 'inventory'))
        self.assertTrue(user_has_capability(self.kitchen, 'kitchen_board'))

    def test_superuser_remains_emergency_all_access(self):
        self.admin.is_superuser = True
        self.admin.is_staff = True
        self.admin.save(update_fields=('is_superuser', 'is_staff'))
        UserCapabilityOverride.objects.create(
            user=self.admin,
            capability='finance',
            allowed=False,
        )
        self.assertTrue(user_has_capability(self.admin, 'finance'))

    def test_inactive_user_has_no_effective_capabilities(self):
        self.waiter.is_active = False
        self.waiter.save(update_fields=('is_active',))
        self.assertFalse(any(get_staff_capabilities(self.waiter).values()))

    def test_edit_form_persists_allow_and_deny_overrides(self):
        form = StaffUserEditForm(
            data={
                'first_name': self.waiter.first_name,
                'last_name': self.waiter.last_name,
                'email': self.waiter.email,
                'phone': self.waiter.phone,
                'role': self.waiter.role,
                'is_active': 'on',
                'capability_allow': ['kitchen_board'],
                'capability_deny': ['reservations'],
            },
            instance=self.waiter,
            actor=self.admin,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertTrue(user_has_capability(self.waiter, 'kitchen_board'))
        self.assertFalse(user_has_capability(self.waiter, 'reservations'))
