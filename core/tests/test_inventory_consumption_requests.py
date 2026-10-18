"""D10 remains blocked: test unposted staff internal-use request handling."""
from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.middleware import VIEW_AS_USER_SESSION_KEY
from core.models import (
    AuditEvent, ActivityLog, CashMovement, InventoryConsumptionRequest,
    InventoryItem, OrderConsumption, PostingBatch, PostingCommand, PostingEntry,
    StockMovement,
)
from core.services.inventory_consumption_requests import (
    allowed_categories, request_inventory_consumption, cancel_consumption_request,
)


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class UnpostedDirectConsumptionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_superuser(
            username='request-owner', phone='985411', password='unused'
        )
        cls.manager = User.objects.create_user(
            username='request-manager', phone='985412', password='unused', role='admin'
        )
        cls.kitchen = User.objects.create_user(
            username='request-kitchen', phone='985413', password='unused', role='kitchen'
        )
        cls.waiter = User.objects.create_user(
            username='request-waiter', phone='985414', password='unused', role='waiter'
        )
        cls.flour = InventoryItem.objects.create(
            name_ar='طحين طلب تجريبي', unit=InventoryItem.Unit.G,
            current_quantity=Decimal('4000.000'), estimated_unit_cost_syp=Decimal('6.00')
        )
        cls.juice = InventoryItem.objects.create(
            name_ar='عصير طلب تجريبي', unit=InventoryItem.Unit.ML,
            current_quantity=Decimal('1000.000'),
        )
        cls.bottles = InventoryItem.objects.create(
            name_ar='زجاجات طلب تجريبي', unit=InventoryItem.Unit.BOTTLE,
            current_quantity=Decimal('5.000'),
        )

    def setUp(self):
        self.url = reverse('staff_inventory_consumption_requests')
        self.client.force_login(self.owner)

    def submit(self, *, user=None, item=None, category='hub_hospitality',
               quantity='1.5', unit='kg', key=None, **data):
        if user:
            self.client.force_login(user)
        return self.client.post(self.url, {
            'action': 'create',
            'inventory_item': str((item or self.flour).pk),
            'category': category, 'quantity': quantity, 'unit': unit,
            'request_key': str(key or uuid.uuid4()),
            'reason': 'ضيافة أو استخدام تجريبي',
            'owner_treatment': 'draw_proposed' if category == 'owner_personal' else '',
            'event_label': 'مهرجان تجربة' if category == 'event' else '',
            **data,
        }, follow=True)

    def assert_no_operational_posting(self):
        self.flour.refresh_from_db()
        self.juice.refresh_from_db()
        self.assertEqual(self.flour.current_quantity, Decimal('4000.000'))
        self.assertEqual(self.juice.current_quantity, Decimal('1000.000'))
        for model in (StockMovement, CashMovement, PostingBatch, PostingCommand, PostingEntry):
            with self.subTest(model=model.__name__):
                self.assertEqual(model.objects.count(), 0)

    def test_kg_request_converts_to_grams_cost_snapshot_without_posting(self):
        response = self.submit()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'لم تُخصم كمية')
        row = InventoryConsumptionRequest.objects.get()
        self.assertEqual(row.state, row.State.PENDING)
        self.assertEqual(row.base_quantity, Decimal('1500.000'))
        self.assertEqual(row.base_unit, 'g')
        self.assertEqual(row.entered_quantity, Decimal('1.5'))
        self.assertEqual(row.entered_unit, 'kg')
        self.assertEqual(row.stock_quantity_at_request, Decimal('4000.000'))
        self.assertEqual(row.unit_cost_estimate_syp, Decimal('6.00'))
        self.assertEqual(row.total_cost_estimate_syp, Decimal('9000.00'))
        self.assertEqual(AuditEvent.objects.filter(action='inventory_consumption_request_created').count(), 1)
        self.assertEqual(ActivityLog.objects.filter(action='inventory_consumption_request_created').count(), 1)
        self.assert_no_operational_posting()

    def test_personal_owner_request_kept_private_from_managers(self):
        response = self.submit(category='owner_personal', owner_treatment='reimburse_proposed')
        self.assertEqual(response.status_code, 200)
        row = InventoryConsumptionRequest.objects.get()
        self.assertEqual(row.owner_treatment, 'reimburse_proposed')
        self.client.force_login(self.manager)
        report = self.client.get(self.url)
        self.assertEqual(report.status_code, 200)
        self.assertNotContains(report, row.reason)
        self.assertNotContains(report, 'تعويض لاحق (بانتظار سياسة التسوية)')
        self.assert_no_operational_posting()

    def test_staff_can_draft_own_use_but_not_owner_or_hospitality(self):
        result = self.submit(user=self.kitchen, category='staff')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(InventoryConsumptionRequest.objects.count(), 1)
        self.assertEqual(InventoryConsumptionRequest.objects.get().requested_by, self.kitchen)
        self.assertNotIn('owner_personal', allowed_categories(self.kitchen))
        self.assertNotIn('hub_hospitality', allowed_categories(self.kitchen))
        for category in ('owner_personal', 'hub_hospitality', 'event'):
            with self.subTest(category=category):
                self.submit(user=self.kitchen, category=category)
                self.assertEqual(InventoryConsumptionRequest.objects.count(), 1)
        self.assert_no_operational_posting()

    def test_waiter_cannot_access_or_inject_direct_request(self):
        self.client.force_login(self.waiter)
        self.assertNotEqual(self.client.get(self.url).status_code, 200)
        with self.assertRaises(PermissionDenied):
            request_inventory_consumption(
                actor=self.waiter, item_id=self.flour.pk, quantity='1',
                unit='kg', category='staff', reason='غير مسموح', request_key=uuid.uuid4(),
            )
        self.assertEqual(InventoryConsumptionRequest.objects.count(), 0)
        self.assert_no_operational_posting()

    def test_volume_unit_conversion_unknown_cost_and_no_package_guess(self):
        response = self.submit(item=self.juice, category='staff', quantity='0.75', unit='liter')
        self.assertEqual(response.status_code, 200)
        row = InventoryConsumptionRequest.objects.get()
        self.assertEqual(row.base_quantity, Decimal('750.000'))
        self.assertIsNone(row.total_cost_estimate_syp)
        self.submit(item=self.bottles, category='hub_hospitality', quantity='1', unit='liter')
        self.assertEqual(InventoryConsumptionRequest.objects.count(), 1)
        self.assert_no_operational_posting()

    def test_invalid_large_or_fractional_quantities_fail_closed(self):
        for qty, unit in [('5', 'kg'), ('-1', 'kg'), ('NaN', 'kg'),
                          ('0', 'kg'), ('0.00000001', 'kg'), ('1', 'liter')]:
            with self.subTest(qty=qty, unit=unit):
                self.submit(quantity=qty, unit=unit)
                self.assertFalse(InventoryConsumptionRequest.objects.exists())
        self.assert_no_operational_posting()

    def test_uuid_is_idempotent_and_cannot_be_reused_with_altered_data(self):
        key = uuid.uuid4()
        self.submit(key=key)
        self.submit(key=key)
        self.assertEqual(InventoryConsumptionRequest.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.count(), 1)
        self.submit(key=key, quantity='1.6')
        self.assertEqual(InventoryConsumptionRequest.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.count(), 1)
        self.assert_no_operational_posting()

    def test_author_cancels_with_reason_and_retry_is_safe(self):
        self.submit(user=self.kitchen, category='staff')
        row = InventoryConsumptionRequest.objects.get()
        request = {'action': 'cancel', 'request_id': row.pk, 'cancellation_reason': 'لم نعد بحاجة للمادة'}
        self.client.post(self.url, request)
        self.client.post(self.url, request)
        row.refresh_from_db()
        self.assertEqual(row.state, row.State.CANCELLED)
        self.assertEqual(row.cancelled_by, self.kitchen)
        self.assertEqual(AuditEvent.objects.filter(action='inventory_consumption_request_cancelled').count(), 1)
        self.assert_no_operational_posting()

    def test_other_staff_cannot_cancel_not_their_request(self):
        self.submit(user=self.manager)
        row = InventoryConsumptionRequest.objects.get()
        self.client.force_login(self.kitchen)
        response = self.client.post(self.url, {
            'action': 'cancel', 'request_id': row.pk,
            'cancellation_reason': 'محاولة غير مرخصة',
        })
        self.assertEqual(response.status_code, 403)
        row.refresh_from_db()
        self.assertEqual(row.state, row.State.PENDING)
        self.assert_no_operational_posting()

    def test_explicit_declaration_required_for_owner_reimbursement_and_event(self):
        self.submit(category='owner_personal', owner_treatment='')
        self.submit(category='event', event_label='')
        self.assertEqual(InventoryConsumptionRequest.objects.count(), 0)
        self.assert_no_operational_posting()

    def test_view_as_prevents_state_mutation(self):
        session = self.client.session
        session[VIEW_AS_USER_SESSION_KEY] = self.kitchen.pk
        session.save()
        response = self.submit()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(InventoryConsumptionRequest.objects.count(), 0)
        self.assert_no_operational_posting()

    def test_d10_disabled_legacy_movement_url_unchanged(self):
        self.client.post(reverse('staff_inventory_movement_new'), {
            'inventory_item': self.flour.pk, 'quantity': '50',
            'movement_type': 'internal_use', 'direction': 'out', 'reason': 'مسودة',
        })
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(InventoryConsumptionRequest.objects.exists())
        self.assert_no_operational_posting()
