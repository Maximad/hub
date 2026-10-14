"""Legacy inventory movements must fail safely while financial D10 is blocked."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import AccessProfile, UserCapabilityOverride
from core.models import (
    ActivityLog, AuditEvent, CashMovement, InventoryItem, PostingBatch,
    PostingCommand, PostingEntry, StockMovement,
)


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class InventoryMovementSafetyTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='movement-admin', password='unused',
            phone='+96319993661', role='admin',
        )
        self.cashier = User.objects.create_user(
            username='movement-cashier', password='unused',
            phone='+96319993662', role='cashier',
        )
        self.kitchen = User.objects.create_user(
            username='movement-kitchen', password='unused',
            phone='+96319993663', role='kitchen',
        )
        self.waiter = User.objects.create_user(
            username='movement-waiter', password='unused',
            phone='+96319993664', role='waiter',
        )
        self.item = InventoryItem.objects.create(
            name_ar='مادة للجرد', unit=InventoryItem.Unit.KG,
            current_quantity=Decimal('8.000'),
            estimated_unit_cost_syp=Decimal('300.00'),
        )
        self.url = reverse('staff_inventory_movement_new')

    def assert_no_changes(self):
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, Decimal('8.000'))
        for model in (
            StockMovement, PostingBatch, PostingEntry, PostingCommand,
            AuditEvent, ActivityLog, CashMovement,
        ):
            with self.subTest(model=model.__name__):
                self.assertFalse(model.objects.exists())

    def submit(self, user, movement_type, direction='out', quantity='2'):
        self.client.force_login(user)
        return self.client.post(self.url, {
            'inventory_item': self.item.pk,
            'business_date': '2026-10-14',
            'movement_type': movement_type,
            'direction': direction,
            'quantity': quantity,
            'reason': 'اختبار فرق مخزون',
        }, follow=True)

    def test_admin_and_cashier_see_safe_guidance_without_post_form(self):
        for user in (self.admin, self.cashier):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'سياسة معالجة')
                self.assertContains(response, 'D10')
                self.assertNotContains(response, 'name="movement_type"')
                self.assertContains(
                    response, reverse('staff_inventory_stock_count'),
                )
                self.assert_no_changes()

    def test_broad_inventory_without_count_cannot_see_count_action(self):
        self.client.force_login(self.kitchen)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="movement_type"')
        self.assertNotContains(
            response, 'href="' + reverse('staff_inventory_stock_count') + '"',
        )
        self.assert_no_changes()

    def test_explicitly_granted_counter_sees_count_action(self):
        profile = AccessProfile.objects.create(
            code='stock-viewer', name_ar='المخزون',
            base_role='kitchen', capabilities=['inventory', 'staff_home'],
        )
        self.kitchen.access_profile = profile
        self.kitchen.save(update_fields=['access_profile'])
        UserCapabilityOverride.objects.create(
            user=self.kitchen, capability='inventory_stock_count', allowed=True,
        )
        self.client.force_login(self.kitchen)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, 'href="' + reverse('staff_inventory_stock_count') + '"',
        )

    def test_posted_waste_internal_use_correction_and_opening_balance_are_rejected(self):
        types = [
            ('waste', 'out'), ('internal_use', 'out'),
            ('manual_adjustment', 'in'), ('correction', 'out'),
            ('opening_balance', 'in'), ('purchase_received', 'in'),
        ]
        for user in (self.admin, self.cashier, self.kitchen):
            for movement_type, direction in types:
                with self.subTest(user=user.username, kind=movement_type):
                    response = self.submit(user, movement_type, direction)
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, 'لم تتغير أي كمية أو قيد مالي')
                    self.assert_no_changes()

    def test_retries_and_bad_fields_do_not_raise_server_error_or_write(self):
        for _ in range(2):
            result = self.submit(
                self.admin, 'unexpected_type', direction='unexpected', quantity='NaN',
            )
            self.assertEqual(result.status_code, 200)
            self.assertContains(result, 'غير مفعّل')
        self.assert_no_changes()

    def test_unpermitted_role_cannot_post_via_direct_url(self):
        result = self.submit(self.waiter, 'waste')
        self.assertEqual(result.status_code, 200)
        self.assert_no_changes()

    def test_inventory_home_and_history_do_not_advertise_live_waste_posting(self):
        self.client.force_login(self.admin)
        home = self.client.get(reverse('staff_inventory_home'))
        self.assertEqual(home.status_code, 200)
        self.assertContains(home, 'تسجيل الهدر والحركات اليدوية متوقف')
        self.assertNotContains(home, '>تسجيل حركة أو هدر</a>')
        history = self.client.get(reverse('staff_inventory_movements'))
        self.assertEqual(history.status_code, 200)
        self.assertNotContains(history, '>حركة جديدة</a>')
        self.assertContains(history, 'إضافة حركة جديدة أو هدر معلقة')
