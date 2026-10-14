"""Read-only inventory staff should not be sent to rejected write actions.

The inventory capability permits viewing; existing legacy role checks still
reserve item creation and supplier purchase drafts for admins/cashiers.
These tests do not touch production data or change any posting policy.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import UserCapabilityOverride
from core.models import InventoryItem, Purchase


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class InventoryRoleActionsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='inventory-actions-admin', phone='+96319995811',
            password='unused', role='admin',
        )
        self.cashier = User.objects.create_user(
            username='inventory-actions-cashier', phone='+96319995812',
            password='unused', role='cashier',
        )
        self.kitchen = User.objects.create_user(
            username='inventory-actions-kitchen', phone='+96319995813',
            password='unused', role='kitchen',
        )
        self.waiter = User.objects.create_user(
            username='inventory-actions-waiter', phone='+96319995814',
            password='unused', role='waiter',
        )
        self.item = InventoryItem.objects.create(
            name_ar='مادة اختبار الصلاحيات',
            unit=InventoryItem.Unit.KG,
            current_quantity=Decimal('3.000'),
        )
        self.new_purchase = reverse('staff_inventory_purchase_new')
        self.new_item = reverse('staff_inventory_item_new')
        self.pages = (
            reverse('staff_inventory_home'),
            reverse('staff_inventory_items'),
            reverse('staff_inventory_purchases'),
        )

    def test_admin_and_cashier_retain_creation_shortcuts(self):
        for user in (self.admin, self.cashier):
            self.client.force_login(user)
            for page in self.pages:
                with self.subTest(role=user.role, page=page):
                    response = self.client.get(page)
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.context['inventory_can_manage'])
                    if page != reverse('staff_inventory_purchases'):
                        self.assertContains(response, f'href="{self.new_item}"')
                    if page != reverse('staff_inventory_items'):
                        self.assertContains(response, f'href="{self.new_purchase}"')

    def assert_read_only_inventory_actions(self, user):
        self.client.force_login(user)
        for page in self.pages:
            with self.subTest(role=user.role, page=page):
                response = self.client.get(page)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.context['inventory_can_manage'])
                self.assertNotContains(response, f'href="{self.new_item}"')
                self.assertNotContains(response, f'href="{self.new_purchase}"')
        self.assertEqual(self.client.get(self.new_item).status_code, 302)
        self.assertEqual(self.client.get(self.new_purchase).status_code, 302)

    def test_kitchen_reads_inventory_without_misleading_creation_actions(self):
        self.assert_read_only_inventory_actions(self.kitchen)

    def test_individually_granted_inventory_does_not_grant_supplier_or_item_writes(self):
        UserCapabilityOverride.objects.create(
            user=self.waiter, capability='inventory', allowed=True,
        )
        self.assert_read_only_inventory_actions(self.waiter)
        self.client.force_login(self.waiter)
        rejected_purchase = self.client.post(self.new_purchase, {
            'supplier_name': 'مورد تجريبي',
            'inventory_item': [str(self.item.pk)],
            'quantity': ['2'],
            'unit_cost_syp': ['100'],
        })
        rejected_item = self.client.post(self.new_item, {
            'name_ar': 'مادة غير مصرح بها',
            'unit': InventoryItem.Unit.KG,
            'item_type': 'ingredient',
        })
        self.assertEqual(rejected_purchase.status_code, 302)
        self.assertEqual(rejected_item.status_code, 302)
        self.assertEqual(InventoryItem.objects.count(), 1)
        self.assertFalse(Purchase.objects.exists())

    def test_stock_count_grant_does_not_expose_purchase_creation(self):
        UserCapabilityOverride.objects.create(
            user=self.kitchen, capability='inventory_stock_count', allowed=True,
        )
        self.client.force_login(self.kitchen)
        response = self.client.get(reverse('staff_inventory_home'))
        self.assertContains(
            response, f'href="{reverse("staff_inventory_stock_count")}"',
        )
        self.assertNotContains(response, f'href="{self.new_purchase}"')
        self.assertNotContains(response, f'href="{self.new_item}"')
