"""Approved D10 stock-only exception; financial posting remains blocked."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from accounts.middleware import VIEW_AS_USER_SESSION_KEY
from core.models import (
    InventoryItem, InventoryConsumptionRequest, StockMovement, AuditEvent,
    Payment, Expense, CashMovement, PostingBatch, PostingEntry, PostingCommand,
)
from core.services.inventory_consumption_requests import (
    request_inventory_consumption, issue_consumption, reverse_consumption,
    cancel_consumption_request, review_consumption_request,
)


class StockFixtures:
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_superuser(username='issue-owner', phone='7001', password='unused')
        self.manager = User.objects.create_user(username='issue-manager', phone='7002', role='admin')
        self.staff = User.objects.create_user(username='issue-staff', phone='7003', role='kitchen')
        self.item = InventoryItem.objects.create(name_ar='طحين التجربة', unit='g',
            current_quantity=Decimal('4000'), estimated_unit_cost_syp=Decimal('2'))
        self.url = reverse('staff_inventory_consumption_requests')

    def draft(self, actor=None, category='staff', **kwargs):
        return request_inventory_consumption(actor=actor or self.staff, item_id=self.item.pk,
            category=category, quantity=kwargs.pop('quantity', '1.5'), unit='kg',
            reason='سبب خاص للاختبار', request_key=uuid.uuid4(), **kwargs)[0]

    def issue(self, record, actor=None, **kwargs):
        return issue_consumption(actor=actor or self.manager, request_id=record.pk,
            direct_issue_confirmed=kwargs.pop('confirmed', True))

    def restore(self, record, **kwargs):
        return reverse_consumption(actor=kwargs.pop('actor', self.manager), request_id=record.pk,
            kind=kwargs.pop('kind', 'physical_return'), reason=kwargs.pop('reason', 'عادت الكمية كاملة'),
            restoration_confirmed=kwargs.pop('confirmed', True))

    def assert_finance_untouched(self):
        for model in (Payment, Expense, CashMovement, PostingBatch, PostingEntry, PostingCommand):
            self.assertEqual(model.objects.count(), 0, model.__name__)


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class ConsumptionStockTests(StockFixtures, TestCase):
    def test_actual_issue_only_once_preserves_snapshot_and_audit(self):
        record = self.draft()
        self.item.estimated_unit_cost_syp = Decimal('9')
        self.item.save()
        row, changed = self.issue(record)
        self.assertTrue(changed)
        self.assertFalse(self.issue(record)[1])
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, Decimal('2500'))
        self.assertEqual(row.total_cost_estimate_syp, Decimal('3000'))
        movement = StockMovement.objects.get()
        self.assertEqual(movement.created_by, self.staff)
        self.assertEqual(movement.approved_by, self.manager)
        self.assertEqual(movement.business_date, row.issue_movement.business_date)
        self.assertEqual(movement.direction, 'out')
        self.assertIsNone(movement.total_value_syp)
        self.assertEqual(AuditEvent.objects.filter(action='inventory_consumption_issued').count(), 1)
        self.assert_finance_untouched()

    def test_staff_and_self_consumption_cannot_authorize(self):
        record = self.draft()
        with self.assertRaises(PermissionDenied):
            self.issue(record, actor=self.staff)
        for actor in (self.manager, self.owner):
            own = self.draft(actor=actor)
            with self.assertRaises(PermissionDenied):
                self.issue(own, actor=actor)
        self.assertFalse(StockMovement.objects.exists())

    def test_manager_cannot_approve_or_return_own_hospitality_or_event_requests(self):
        for category, extras in (
            ('hub_hospitality', {}),
            ('event', {'event_label': 'ورشة'}),
        ):
            with self.subTest(category=category):
                record = self.draft(actor=self.manager, category=category, **extras)
                with self.assertRaises(PermissionDenied):
                    self.issue(record, actor=self.manager)
                self.client.force_login(self.manager)
                page = self.client.get(self.url)
                visible = next(r for r in page.context['rows'] if r.pk == record.pk)
                self.assertFalse(visible.can_issue)
                self.issue(record, actor=self.owner)
                page = self.client.get(self.url)
                visible = next(r for r in page.context['rows'] if r.pk == record.pk)
                self.assertFalse(visible.can_return)
                with self.assertRaises(PermissionDenied):
                    self.restore(record, actor=self.manager)
                self.restore(record, actor=self.owner)
        self.assert_finance_untouched()

    def test_superuser_cannot_approve_own_hub_hospitality(self):
        record = self.draft(actor=self.owner, category='hub_hospitality')
        with self.assertRaises(PermissionDenied):
            self.issue(record, actor=self.owner)
        self.client.force_login(self.owner)
        row = next(r for r in self.client.get(self.url).context['rows'] if r.pk == record.pk)
        self.assertFalse(row.can_issue)
        self.assert_finance_untouched()

    def test_owner_personal_is_private_and_self_confirmable_without_treatment(self):
        record = self.draft(actor=self.owner, category='owner_personal')
        with self.assertRaises(PermissionDenied):
            self.issue(record)
        self.issue(record, actor=self.owner)
        with self.assertRaises(PermissionDenied):
            self.restore(record)
        self.client.force_login(self.manager)
        response = self.client.get(self.url)
        self.assertNotContains(response, record.reason)
        self.assertEqual(response.context['consumption_totals'], [])
        self.owner.is_superuser = False
        self.owner.role = 'admin'
        self.owner.save()
        with self.assertRaises(PermissionDenied):
            self.restore(record, actor=self.owner)
        self.assert_finance_untouched()

    def test_confirm_required_and_current_stock_rechecked(self):
        record = self.draft()
        with self.assertRaises(ValidationError):
            self.issue(record, confirmed=False)
        self.item.current_quantity = Decimal('100')
        self.item.save()
        with self.assertRaises(ValidationError):
            self.issue(record)
        self.assertFalse(StockMovement.objects.exists())
        record.refresh_from_db()
        self.assertEqual(record.state, record.State.PENDING)

    def test_changed_unit_and_inactive_item_block_issue(self):
        record = self.draft()
        self.item.unit = 'kg'
        self.item.save()
        with self.assertRaises(ValidationError):
            self.issue(record)
        self.item.unit = 'g'
        self.item.is_active = False
        self.item.save()
        with self.assertRaises(ValidationError):
            self.issue(record)
        self.assertFalse(StockMovement.objects.exists())

    def test_unknown_cost_does_not_block_stock_or_turn_into_zero_cost(self):
        self.item.estimated_unit_cost_syp = None
        self.item.save()
        record = self.draft()
        self.issue(record)
        self.client.force_login(self.manager)
        response = self.client.get(self.url)
        self.assertIsNone(record.total_cost_estimate_syp)
        self.assertContains(response, 'بكلفة مجهولة')
        self.assertEqual(response.context['consumption_totals'][0]['unknown'], 1)
        self.assert_finance_untouched()

    def test_full_return_preserves_original_and_issue_replay_does_not_rededuct(self):
        record = self.draft()
        self.issue(record)
        original = StockMovement.objects.values().get()
        self.restore(record)
        self.assertFalse(self.restore(record)[1])
        self.assertFalse(self.issue(record)[1])
        record.refresh_from_db()
        self.assertEqual(record.state, record.State.REVERSED)
        self.assertEqual(StockMovement.objects.values().get(pk=record.issue_movement_id), original)
        self.assertEqual(StockMovement.objects.count(), 2)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, Decimal('4000'))
        self.assertEqual(AuditEvent.objects.filter(action='inventory_consumption_reversed').count(), 1)
        self.assert_finance_untouched()

    def test_invalid_return_reason_confirmation_and_unissued_return(self):
        record = self.draft()
        with self.assertRaises(ValidationError):
            self.restore(record)
        self.issue(record)
        for kwargs in ({'confirmed': False}, {'reason': ''}, {'kind': 'partial'}):
            with self.assertRaises(ValidationError):
                self.restore(record, **kwargs)
        self.assertEqual(StockMovement.objects.count(), 1)
        self.restore(record, kind='entry_error')
        self.assert_finance_untouched()

    def test_cancelled_request_cannot_issue_and_issued_cannot_cancel(self):
        record = self.draft()
        cancel_consumption_request(actor=self.staff, request_id=record.pk, reason='غير مطلوب')
        with self.assertRaises(ValidationError):
            self.issue(record)
        issued = self.draft()
        self.issue(issued)
        with self.assertRaises(ValidationError):
            cancel_consumption_request(actor=self.staff, request_id=issued.pk, reason='محاولة')

    def test_audit_failure_rolls_back_issue_and_return(self):
        record = self.draft()
        with patch.object(AuditEvent.objects, 'create', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.issue(record)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, Decimal('4000'))
        self.assertFalse(StockMovement.objects.exists())
        self.issue(record)
        with patch.object(AuditEvent.objects, 'create', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.restore(record)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, Decimal('2500'))
        self.assertEqual(StockMovement.objects.count(), 1)

    def test_reviewed_legacy_draft_requires_individual_issue_confirmation(self):
        record = self.draft()
        review_consumption_request(actor=self.manager, request_id=record.pk, note='مراجعة قديمة')
        self.assertFalse(StockMovement.objects.exists())
        self.issue(record)
        record.refresh_from_db()
        self.assertEqual(record.review_note, 'مراجعة قديمة')
        self.assertEqual(record.state, record.State.ISSUED)

    def test_month_report_uses_issue_and_return_dates_not_draft_date(self):
        record = self.draft()
        with patch('core.services.inventory_consumption_requests.timezone.localdate', return_value=date(2026, 1, 31)):
            self.issue(record)
        with patch('core.services.inventory_consumption_requests.timezone.localdate', return_value=date(2026, 2, 1)):
            self.restore(record)
        self.client.force_login(self.manager)
        jan = self.client.get(self.url, {'month': '2026-01'}).context['consumption_totals'][0]
        feb = self.client.get(self.url, {'month': '2026-02'}).context['consumption_totals'][0]
        self.assertEqual(jan['net'], Decimal('1500'))
        self.assertEqual(feb['net'], Decimal('-1500'))
        self.assertEqual(jan['cost'], Decimal('3000'))
        self.assertEqual(feb['cost'], Decimal('-3000'))
        self.assert_finance_untouched()

    def test_endpoint_issue_return_view_as_and_get_are_safe(self):
        record = self.draft()
        self.client.force_login(self.owner)
        session = self.client.session
        session[VIEW_AS_USER_SESSION_KEY] = self.manager.pk
        session.save()
        payload = {'action': 'issue', 'request_id': record.pk, 'direct_issue_confirmed': 'yes'}
        self.assertEqual(self.client.post(self.url, payload).status_code, 403)
        session = self.client.session
        session.pop(VIEW_AS_USER_SESSION_KEY)
        session.save()
        self.client.get(self.url, payload)
        self.assertFalse(StockMovement.objects.exists())
        self.assertEqual(self.client.post(self.url, payload).status_code, 302)
        self.assertEqual(self.client.post(self.url, {'action': 'return', 'request_id': record.pk,
            'kind': 'entry_error', 'reason': 'لم تصرف', 'restoration_confirmed': 'yes'}).status_code, 302)
        self.assertEqual(StockMovement.objects.count(), 2)
        self.assert_finance_untouched()


class ConsumptionStockConcurrencyTests(StockFixtures, TransactionTestCase):
    def race(self, tasks):
        barrier = Barrier(len(tasks))
        def run(task):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    return task()
                except ValidationError:
                    return 'rejected'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            return list(pool.map(run, tasks))

    def test_duplicate_issue_and_return_are_once_only(self):
        record = self.draft()
        results = self.race([lambda: self.issue(record)[1]] * 2)
        self.assertEqual(sorted(results), [False, True])
        results = self.race([lambda: self.restore(record)[1]] * 2)
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(StockMovement.objects.count(), 2)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, Decimal('4000'))
        self.assert_finance_untouched()

    def test_competing_requests_cannot_overdraw_inventory(self):
        first = self.draft(quantity='3')
        second = self.draft(quantity='3')
        results = self.race([lambda: self.issue(first)[1], lambda: self.issue(second)[1]])
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count('rejected'), 1)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, Decimal('1000'))
        self.assertEqual(StockMovement.objects.count(), 1)
        self.assert_finance_untouched()
