"""D10 draft-only request flow. Never touches stock, finance or posting."""
from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import (
    InventoryItem, InternalConsumptionRequest, StockMovement, PostingBatch,
    CashMovement, Payment, ActivityLog,
)


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class InternalConsumptionDraftTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_superuser(
            username='d10-draft-owner', phone='973711', password='SecureTest#111'
        )
        cls.manager = User.objects.create_user(
            username='d10-draft-admin', phone='973712', password='SecureTest#111',
            role='admin',
        )
        cls.staff = User.objects.create_user(
            username='d10-draft-kitchen', phone='973713', password='SecureTest#111',
            role='kitchen',
        )
        cls.grams = InventoryItem.objects.create(
            name_ar='دقيق للتجربة', unit=InventoryItem.Unit.G,
            current_quantity=Decimal('9000.000'), estimated_unit_cost_syp=Decimal('2'),
        )
        cls.bottles = InventoryItem.objects.create(
            name_ar='زجاجة عصير اختبار', unit=InventoryItem.Unit.BOTTLE,
            current_quantity=Decimal('7.000'),
        )
        cls.url = reverse('staff_internal_consumption_requests')

    def setUp(self):
        self.client.force_login(self.owner)

    def submit(self, **kwargs):
        form={
            'request_key':str(uuid.uuid4()),
            'inventory_item_id':str(self.grams.pk),
            'quantity':'1.5', 'unit':'kg', 'category':'hub_hospitality',
            'purpose':'ضيافة ورشة', 'beneficiary_note':'ضيوف هَب',
        }
        form.update(kwargs)
        return self.client.post(self.url,form)

    def assert_stock_untouched(self):
        self.grams.refresh_from_db()
        self.bottles.refresh_from_db()
        self.assertEqual(self.grams.current_quantity,Decimal('9000.000'))
        self.assertEqual(self.bottles.current_quantity,Decimal('7.000'))
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(PostingBatch.objects.exists())
        self.assertFalse(CashMovement.objects.exists())
        self.assertFalse(Payment.objects.exists())

    def test_draft_kg_converts_to_grams_without_deduction_or_finance(self):
        self.assertContains(self.client.get(self.url), 'حفظ الطلب دون خصم مخزون')
        result=self.submit()
        self.assertRedirects(result,self.url)
        self.assertEqual(InternalConsumptionRequest.objects.count(),1)
        row=InternalConsumptionRequest.objects.get()
        self.assertEqual(row.entered_quantity,Decimal('1.500000'))
        self.assertEqual(row.base_quantity,Decimal('1500.000'))
        self.assertEqual(row.base_unit,'g')
        self.assertEqual(row.status,'submitted')
        self.assertEqual(row.estimated_unit_cost_syp_snapshot,Decimal('2'))
        self.assert_stock_untouched()

    def test_review_is_not_stock_approval_and_duplicate_review_is_noop(self):
        self.submit()
        row=InternalConsumptionRequest.objects.get()
        url=reverse('staff_internal_consumption_review',args=[row.pk])
        self.client.force_login(self.manager)
        self.client.post(url,{'action':'review','note':'تم الاطلاع دون خصم'})
        row.refresh_from_db()
        self.assertEqual(row.status,'reviewed')
        self.assertEqual(row.reviewed_by,self.manager)
        self.assertEqual(ActivityLog.objects.filter(action='consumption_request_reviewed_without_posting').count(),1)
        self.client.post(url,{'action':'review','note':'تكرار'})
        self.assertEqual(ActivityLog.objects.filter(action='consumption_request_reviewed_without_posting').count(),1)
        self.assert_stock_untouched()

    def test_cancelled_draft_does_not_change_inventory(self):
        self.submit()
        row=InternalConsumptionRequest.objects.get()
        self.client.post(reverse('staff_internal_consumption_review',args=[row.pk]), {
            'action':'cancel','note':'سحب الطلب قبل التسوية'
        })
        row.refresh_from_db()
        self.assertEqual(row.status,'cancelled')
        self.assert_stock_untouched()

    def test_idempotency_key_retry_does_not_double_create(self):
        key=str(uuid.uuid4())
        self.submit(request_key=key)
        self.submit(request_key=key)
        self.assertEqual(InternalConsumptionRequest.objects.count(),1)
        self.assertEqual(ActivityLog.objects.filter(action='consumption_request_draft_created').count(),1)
        self.assert_stock_untouched()

    def test_owner_personal_not_available_to_staff_or_managers(self):
        self.client.force_login(self.manager)
        self.submit(category='owner_personal')
        self.assertFalse(InternalConsumptionRequest.objects.exists())
        self.client.force_login(self.owner)
        self.submit(category='owner_personal')
        self.assertEqual(InternalConsumptionRequest.objects.count(),1)
        self.assert_stock_untouched()

    def test_incompatible_unit_and_fractional_piece_rejected(self):
        for data in [
            {'unit':'liter'}, {'unit':'box'}, {'quantity':'NaN'}, {'quantity':'0'},
            {'quantity':'-2'}, {'quantity':'0.00000001'},
            {'inventory_item_id':str(self.bottles.pk),'quantity':'1.5','unit':'bottle'},
        ]:
            with self.subTest(data=data):
                self.submit(**data)
                self.assertEqual(InternalConsumptionRequest.objects.count(),0)
        self.assert_stock_untouched()

    def test_valid_g_to_kg_and_tiny_fraction_are_exact(self):
        self.submit(quantity='0.000001',unit='kg')
        row=InternalConsumptionRequest.objects.get()
        self.assertEqual(row.entered_quantity,Decimal('0.000001'))
        self.assertEqual(row.base_quantity,Decimal('0.001'))
        self.assert_stock_untouched()
