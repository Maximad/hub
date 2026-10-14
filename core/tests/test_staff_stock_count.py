"""Contract tests for restricted physical count, concurrency and no money writes."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import AccessProfile, UserCapabilityOverride
from accounts.permissions import user_has_capability
from core.models import (
    ActivityLog, AuditEvent, CashMovement, InventoryItem, Payment,
    PostingBatch, PostingCommand, StockMovement,
)


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class StaffStockCountTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(username='count-admin', password='x', phone='+9631999511', role='admin')
        self.cashier = User.objects.create_user(username='count-cashier', password='x', phone='+9631999512', role='cashier')
        self.kitchen = User.objects.create_user(username='count-kitchen', password='x', phone='+9631999513', role='kitchen')
        self.waiter = User.objects.create_user(username='count-waiter', password='x', phone='+9631999514', role='waiter')
        self.item = InventoryItem.objects.create(
            name_ar='قهوة سادة', unit=InventoryItem.Unit.KG,
            current_quantity=Decimal('3.000'), estimated_unit_cost_syp=Decimal('200.00'),
        )
        self.url = reverse('staff_inventory_stock_count')

    def preview(self, user=None, *, counted='2.500', reason='فرق جرد', item=None):
        self.client.force_login(user or self.cashier)
        return self.client.post(self.url, {
            'action': 'preview',
            'item': str((item or self.item).pk),
            'count': counted,
            'reason': reason,
        })

    def confirm(self, token, user=None):
        self.client.force_login(user or self.cashier)
        return self.client.post(self.url, {
            'action': 'confirm', 'confirmation_token': token,
        })

    def test_admin_and_cashier_can_count_without_generalized_finance_rights(self):
        for user in (self.admin, self.cashier):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                self.assertTrue(user_has_capability(user, 'inventory_stock_count'))
                result = self.client.get(self.url)
                self.assertEqual(result.status_code, 200)
                self.assertContains(result, 'جرد المخزون')

    def test_kitchen_waiter_and_broad_inventory_alone_cannot_count(self):
        for user in (self.kitchen, self.waiter):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                self.assertFalse(user_has_capability(user, 'inventory_stock_count'))
                self.assertEqual(self.client.get(self.url).status_code, 302)
                self.assertEqual(self.preview(user).status_code, 302)
        self.assertFalse(StockMovement.objects.exists())

    def test_profile_overrides_grant_and_deny_exact_permission(self):
        profile = AccessProfile.objects.create(
            code='prep-with-inventory', name_ar='تحضير',
            base_role='kitchen', capabilities=['staff_home', 'inventory'],
        )
        self.kitchen.access_profile = profile
        self.kitchen.save(update_fields=['access_profile'])
        self.assertFalse(user_has_capability(self.kitchen, 'inventory_stock_count'))
        UserCapabilityOverride.objects.create(
            user=self.kitchen, capability='inventory_stock_count', allowed=True,
        )
        self.assertTrue(user_has_capability(self.kitchen, 'inventory_stock_count'))
        self.client.force_login(self.kitchen)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        UserCapabilityOverride.objects.filter(user=self.kitchen, capability='inventory_stock_count').update(allowed=False)
        self.assertFalse(user_has_capability(self.kitchen, 'inventory_stock_count'))
        self.assertEqual(self.client.get(self.url).status_code, 302)

        cashier_profile = AccessProfile.objects.create(
            code='custom-cashier', name_ar='تشغيل وكاشير',
            base_role='cashier', capabilities=['staff_home', 'inventory_stock_count'],
        )
        self.cashier.access_profile = cashier_profile
        self.cashier.save(update_fields=['access_profile'])
        self.assertTrue(user_has_capability(self.cashier, 'inventory_stock_count'))
        UserCapabilityOverride.objects.create(
            user=self.cashier, capability='inventory_stock_count', allowed=False,
        )
        self.assertFalse(user_has_capability(self.cashier, 'inventory_stock_count'))

    def test_preview_does_not_modify_stock_or_cash(self):
        preview = self.preview()
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.context['preview']['difference'], Decimal('-0.500'))
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, Decimal('3.000'))
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(CashMovement.objects.exists())
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(PostingBatch.objects.exists())

    def test_confirm_applies_single_audited_correction_and_is_idempotent(self):
        preview = self.preview()
        token = preview.context['preview']['token']
        self.assertEqual(self.confirm(token).status_code, 302)
        self.assertEqual(self.confirm(token).status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, Decimal('2.500'))
        movements = StockMovement.objects.filter(inventory_item=self.item)
        self.assertEqual(movements.count(), 1)
        movement = movements.get()
        self.assertEqual(movement.movement_type, StockMovement.MovementType.CORRECTION)
        self.assertEqual(movement.direction, StockMovement.Direction.OUT)
        self.assertEqual(movement.quantity, Decimal('0.500'))
        self.assertEqual(movement.unit, InventoryItem.Unit.KG)
        self.assertEqual(movement.total_value_syp, Decimal('100.00'))
        self.assertEqual(movement.created_by_id, self.cashier.pk)
        self.assertEqual(PostingCommand.objects.filter(command='inventory.stock_count').count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action='inventory_physical_count_confirmed').count(), 1)
        self.assertEqual(ActivityLog.objects.filter(action='inventory_stock_count_confirmed').count(), 1)
        self.assertFalse(CashMovement.objects.exists())
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(PostingBatch.objects.exists())

    def test_zero_is_valid_blank_is_not_and_unchanged_creates_no_movement(self):
        invalid = self.preview(counted='')
        self.assertEqual(invalid.status_code, 200)
        self.assertIsNone(invalid.context['preview'])
        self.assertContains(invalid, 'أدخل الكمية')
        same = self.preview(counted='3', reason='')
        self.assertEqual(same.status_code, 200)
        self.assertEqual(self.confirm(same.context['preview']['token']).status_code, 302)
        self.assertEqual(StockMovement.objects.count(), 0)
        empty = self.preview(counted='0', reason='تم الجرد والمادة نفدت')
        self.assertEqual(self.confirm(empty.context['preview']['token']).status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, Decimal('0'))

    def test_requires_reason_and_rejects_invalid_precision(self):
        for count, reason in [('2', ''), ('-1', 'تصحيح'), ('1.2345', 'تصحيح'), ('abc', 'تصحيح')]:
            response = self.preview(counted=count, reason=reason)
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(response.context['preview'])
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, Decimal('3.000'))

    def test_stale_stock_refuses_correction_and_keeps_entered_history(self):
        preview = self.preview(counted='2', reason='اختلاف')
        token = preview.context['preview']['token']
        InventoryItem.objects.filter(pk=self.item.pk).update(current_quantity=Decimal('4.000'))
        response = self.confirm(token)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'تغير مخزون المادة')
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, Decimal('4.000'))
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(PostingCommand.objects.exists())

    def test_supplier_payment_approval_is_progressively_disclosed(self):
        from django.utils import timezone
        from core.models import Purchase

        purchase = Purchase.objects.create(
            business_date=timezone.localdate(),
            supplier_name='مورد للاختبار',
            created_by=self.admin,
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse('staff_purchase_detail', args=[purchase.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-purchase-payment-form')
        self.assertContains(response, 'data-purchase-payment-amount')
        self.assertContains(response, 'data-purchase-approval')
        self.assertContains(response, 'js/hub_adaptive_forms.js')
        self.assertContains(response, 'name="approver"')

    def test_confirmation_bound_to_actor_and_original_unit(self):
        preview = self.preview()
        token = preview.context['preview']['token']
        response = self.confirm(token, user=self.admin)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'انتهت صلاحية المراجعة')
        self.assertFalse(StockMovement.objects.exists())
        InventoryItem.objects.filter(pk=self.item.pk).update(unit=InventoryItem.Unit.G)
        response = self.confirm(token)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'تغير مخزون المادة')
        self.assertFalse(StockMovement.objects.exists())
