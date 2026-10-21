"""Stock count snapshots remain distinct from subsequently recorded balances."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserCapabilityOverride
from core.models import (
    ActivityLog, AuditEvent, InventoryItem, Payment, PostingBatch,
    Purchase, PurchaseItem, PurchaseReceipt, PurchaseReceiptLine, StockMovement,
)
from core.services.inventory_provenance import quantity_provenance
from operations.models import BusinessDayTask, OperationalReviewCase


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class InventoryProvenanceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.cashier = User.objects.create_user(username='provenance-cashier', phone='+963955880001', role='cashier')
        self.waiter = User.objects.create_user(username='provenance-waiter', phone='+963955880002', role='waiter')
        self.item = InventoryItem.objects.create(name_ar='بن للجرد', code='PROVENANCE-BEAN', unit='kg', current_quantity=Decimal('3'))
        self.other = InventoryItem.objects.create(name_ar='سكر آخر', code='PROVENANCE-SUGAR', unit='kg')
        self.client.force_login(self.cashier)
        self.count_url = reverse('staff_inventory_stock_count')
        self.history_url = reverse('staff_inventory_movements')

    def count(self, quantity='3'):
        preview = self.client.post(self.count_url, {
            'action': 'preview', 'item': self.item.pk, 'count': quantity, 'reason': 'جرد فعلي',
        })
        self.assertEqual(preview.status_code, 200)
        token = preview.context['preview']['token']
        self.assertEqual(self.client.post(self.count_url, {'action': 'confirm', 'confirmation_token': token}).status_code, 302)
        self.item.refresh_from_db()
        return AuditEvent.objects.filter(channel='staff-stock-count').latest('pk'), token

    def movement(self, **kwargs):
        values = dict(inventory_item=self.item, business_date=timezone.localdate(),
            movement_type='manual_adjustment', direction='in', quantity=Decimal('1'), unit='kg')
        values.update(kwargs)
        return StockMovement.objects.create(**values)

    def provenance(self):
        self.item.refresh_from_db()
        return quantity_provenance([self.item])[self.item.pk]

    def test_legacy_zero_cost_and_activity_entry_do_not_supply_a_quantity_snapshot(self):
        self.item.current_quantity = 0
        self.item.estimated_unit_cost_syp = 200
        self.item.save()
        ActivityLog.objects.create(action='inventory_stock_count_confirmed', details={'item_id': self.item.pk})
        result = self.provenance()
        self.assertEqual(result['state'], 'no_snapshot')
        self.assertIsNone(result['counted_quantity'])
        self.assertIsNone(result['counted_at'])

    def test_real_unchanged_zero_count_is_displayed_and_duplicate_confirmation_is_harmless(self):
        audit, token = self.count('0')
        # Repeat a real *unchanged* zero count after the first correction.
        audit, token = self.count('0')
        self.client.post(self.count_url, {'action': 'confirm', 'confirmation_token': token})
        result = self.provenance()
        self.assertEqual(result['counted_quantity'], Decimal('0'))
        self.assertEqual(result['state'], 'matches_count')
        self.assertEqual(result['count_id'], audit.pk)
        self.assertFalse(result['has_later_movements'])
        self.assertEqual(AuditEvent.objects.filter(channel='staff-stock-count').count(), 2)
        page = self.client.get(self.count_url, {'item': self.item.pk})
        self.assertContains(page, 'آخر جرد موثّق')
        self.assertContains(page, 'الرصيد يطابق آخر جرد')

    def test_count_correction_itself_is_not_reported_as_a_later_movement(self):
        self.count('2')
        self.assertEqual(StockMovement.objects.count(), 1)
        result = self.provenance()
        self.assertEqual(result['state'], 'matches_count')
        self.assertFalse(result['has_later_movements'])

    def test_backdated_receipt_record_is_visible_without_claiming_application(self):
        old = self.movement(reason='حركة قديمة')
        audit, _ = self.count()
        purchase = Purchase.objects.create(business_date=timezone.localdate() - timedelta(days=10))
        line = PurchaseItem.objects.create(purchase=purchase, inventory_item=self.item, quantity=1, unit='kg', unit_cost_syp=100)
        receipt = PurchaseReceipt.objects.create(purchase=purchase, business_date=purchase.business_date, actor=self.cashier, idempotency_key='provenance-receipt')
        receipt_line = PurchaseReceiptLine.objects.create(receipt=receipt, purchase_item=line, received_quantity=1)
        received = self.movement(business_date=purchase.business_date, movement_type='purchase_received',
            related_purchase=purchase, related_purchase_item=line, purchase_receipt_line=receipt_line)
        result = self.provenance()
        self.assertEqual(result['state'], 'later_movements')
        self.assertTrue(result['matches_count'])  # Only recorded, not applied.
        page = self.client.get(self.history_url, {'item': self.item.pk, 'count': audit.pk})
        self.assertEqual([m.pk for m in page.context['movements']], [received.pk])
        self.assertNotContains(page, 'حركة قديمة')
        self.assertContains(page, reverse('staff_inventory_purchase_detail', args=[purchase.pk]))
        self.assertContains(page, 'مرتبط ببند استلام موثّق')
        self.assertContains(page, 'تاريخ العمل')
        received.apply_to_stock()
        self.assertFalse(self.provenance()['matches_count'])
        self.assertTrue(StockMovement.objects.filter(pk=old.pk).exists())

    def test_cancelled_records_are_labelled_and_do_not_disappear_from_provenance(self):
        audit, _ = self.count()
        movement = self.movement(is_cancelled=True, cancellation_reason='أُلغي قبل التطبيق')
        result = self.provenance()
        self.assertEqual(result['state'], 'later_movements')
        self.assertTrue(result['latest_movement']['is_cancelled'])
        page = self.client.get(self.history_url, {'item': self.item.pk, 'count': audit.pk})
        self.assertEqual([m.pk for m in page.context['movements']], [movement.pk])
        self.assertContains(page, 'أُلغي قبل التطبيق')

    def test_unlogged_balance_change_and_unit_change_are_separate_conditions(self):
        self.count()
        InventoryItem.objects.filter(pk=self.item.pk).update(current_quantity=Decimal('2'))
        self.assertEqual(self.provenance()['state'], 'quantity_differs')
        InventoryItem.objects.filter(pk=self.item.pk).update(unit='g')
        result = self.provenance()
        self.assertEqual(result['state'], 'unit_changed')
        self.assertEqual(result['counted_quantity'], Decimal('3'))
        self.assertEqual(result['counted_unit'], 'كغ')

    def test_a_new_count_resets_the_baseline_without_changing_old_records(self):
        old_audit, _ = self.count()
        movement = self.movement()
        movement.apply_to_stock()
        new_audit, _ = self.count('4')
        result = self.provenance()
        self.assertEqual(result['count_id'], new_audit.pk)
        self.assertEqual(result['state'], 'matches_count')
        page = self.client.get(self.history_url, {'item': self.item.pk, 'count': new_audit.pk})
        self.assertEqual(list(page.context['movements']), [])
        self.assertTrue(AuditEvent.objects.filter(pk=old_audit.pk).exists())
        self.assertTrue(StockMovement.objects.filter(pk=movement.pk).exists())

    def test_latest_incomplete_snapshot_is_not_replaced_by_an_older_valid_one(self):
        self.count()
        latest = AuditEvent.objects.create(action='inventory_physical_count_verified', channel='staff-stock-count',
            before_snapshot={'item_id': self.item.pk, 'unit': ['bad-unit']}, after_snapshot=['bad-shape'])
        result = self.provenance()
        self.assertEqual(result['count_id'], latest.pk)
        self.assertEqual(result['state'], 'incomplete_snapshot')
        self.assertIsNone(result['counted_quantity'])

    def test_unrelated_audit_channels_and_non_count_actions_are_ignored(self):
        for action, channel in [('inventory_physical_count_verified', 'import'), ('something_else', 'staff-stock-count')]:
            AuditEvent.objects.create(action=action, channel=channel,
                before_snapshot={'item_id': self.item.pk, 'unit': 'kg'}, after_snapshot={'quantity': '3'})
        self.assertEqual(self.provenance()['state'], 'no_snapshot')

    def test_batch_read_is_two_queries_and_ties_use_newest_primary_key(self):
        first, _ = self.count()
        newest = AuditEvent.objects.create(action='inventory_physical_count_verified', channel='staff-stock-count',
            before_snapshot={'item_id': self.item.pk, 'unit': 'kg'}, after_snapshot={'quantity': '2'})
        AuditEvent.objects.filter(pk=newest.pk).update(created_at=first.created_at)
        items = [self.item, self.other]
        for number in range(15):
            items.append(InventoryItem.objects.create(name_ar=f'مادة {number}', unit='piece'))
        with self.assertNumQueries(2):
            result = quantity_provenance(items)
        self.assertEqual(result[self.item.pk]['count_id'], newest.pk)
        self.assertEqual(len(result), 17)
        with self.assertNumQueries(0):
            self.assertEqual(quantity_provenance([]), {})

    def test_history_filters_reject_wrong_item_and_invalid_ids_without_broadening(self):
        audit, _ = self.count()
        self.movement(inventory_item=self.other, reason='OTHER-MATERIAL-PRIVATE-ROW')
        for params in ({'item': self.other.pk, 'count': audit.pk}, {'count': audit.pk},
                       {'item': 'bad'}, {'item': self.item.pk, 'count': 'bad'},
                       {'item': self.item.pk, 'count': '9' * 100}):
            page = self.client.get(self.history_url, params)
            self.assertEqual(page.status_code, 200)
            self.assertEqual(list(page.context['movements']), [])
            self.assertNotContains(page, 'OTHER-MATERIAL-PRIVATE-ROW')
            self.assertContains(page, 'role="alert"')

    def test_permissions_and_count_only_access_do_not_expose_movement_notes_or_cost(self):
        self.count()
        self.movement(reason='PRIVATE-RECEIPT-NOTE', unit_cost_syp=987654)
        self.client.force_login(self.waiter)
        for url in (self.count_url, self.history_url, reverse('staff_inventory_items')):
            self.assertEqual(self.client.get(url, {'item': self.item.pk}).status_code, 302)
        UserCapabilityOverride.objects.create(user=self.waiter, capability='inventory_stock_count', allowed=True)
        page = self.client.get(self.count_url, {'item': self.item.pk})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'حركات مسجّلة بعد الجرد')
        self.assertNotContains(page, 'PRIVATE-RECEIPT-NOTE')
        self.assertNotContains(page, '987654')
        self.assertNotContains(page, '?item=' + str(self.item.pk) + '&amp;count=')
        self.assertNotContains(page, 'href="' + self.history_url)
        self.assertEqual(self.client.get(self.history_url, {'item': self.item.pk}).status_code, 302)

    def test_repeated_reads_do_not_write_sources_tasks_reviews_or_money(self):
        audit, _ = self.count()
        models = [ActivityLog, AuditEvent, StockMovement, Payment, PostingBatch, BusinessDayTask, OperationalReviewCase]
        before = [model.objects.count() for model in models]
        for _ in range(2):
            for url, params in ((self.count_url, {'item': self.item.pk}),
                                (self.history_url, {'item': self.item.pk, 'count': audit.pk}),
                                (reverse('staff_inventory_items'), {})):
                self.assertEqual(self.client.get(url, params).status_code, 200)
        self.assertEqual([model.objects.count() for model in models], before)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, Decimal('3'))
