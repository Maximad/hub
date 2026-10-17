"""Pilot complimentary POS items and owner personal consumption safety rules."""
from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.middleware import VIEW_AS_USER_SESSION_KEY
from core.models import (
    ActivityLog, AuditEvent, CashMovement, Category, FinancialAccount, HubVisit,
    Order, OrderConsumption, OrderDiscount, OrderItem, Payment, PostingBatch,
    Product, StockMovement,
)
from core.services.order_consumption import record_consumption


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class PosConsumptionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_superuser(
            username='consumption-owner', phone='982181', password='TestOwner#2099'
        )
        cls.manager = User.objects.create_user(
            username='consumption-manager', phone='982182', password='TestManager#2099', role='admin'
        )
        cls.cashier = User.objects.create_user(
            username='consumption-cashier', phone='982183', password='TestCashier#2099', role='cashier'
        )
        cls.category = Category.objects.create(name_ar='مشروبات تجربة')
        cls.coffee = Product.objects.create(
            category=cls.category, name_ar='كابتشينو تجربة', price_syp=90,
            product_type=Product.ProductType.DRINK,
            item_type=Product.ItemType.BEVERAGE,
            estimated_unit_cost_syp=Decimal('32'),
        )
        cls.sandwich = Product.objects.create(
            category=cls.category, name_ar='سندويش تجربة', price_syp=150,
            product_type=Product.ProductType.FOOD,
            item_type=Product.ItemType.FOOD,
        )
        cls.internet = Product.objects.create(
            category=cls.category, name_ar='إنترنت لا يقبل الضيافة', price_syp=120,
            product_type=Product.ProductType.INTERNET,
            item_type=Product.ItemType.SERVICE, not_discountable=True,
        )

    def setUp(self):
        self.order = Order.objects.create()
        self.coffee_line = OrderItem.objects.create(
            order=self.order, product=self.coffee, quantity=2,
            product_name_ar_snapshot=self.coffee.name_ar,
            unit_price_syp_snapshot=90, line_total_syp_snapshot=180,
            estimated_unit_cost_syp_snapshot=32,
            estimated_line_cost_syp_snapshot=64,
            prep_status=OrderItem.PrepStatus.NO_PREP,
        )
        self.sandwich_line = OrderItem.objects.create(
            order=self.order, product=self.sandwich, quantity=1,
            product_name_ar_snapshot=self.sandwich.name_ar,
            unit_price_syp_snapshot=150, line_total_syp_snapshot=150,
            prep_status=OrderItem.PrepStatus.NO_PREP,
        )
        self.url = reverse('staff_order_consumption', args=[self.order.public_code])
        self.client.force_login(self.owner)

    def post(self, *, user=None, line=None, category='hub_hospitality', qty=1,
             key=None, **kwargs):
        if user:
            self.client.force_login(user)
        return self.client.post(self.url, {
            'order_item_id': str((line or self.coffee_line).pk),
            'quantity': str(qty), 'category': category,
            'request_key': str(key or uuid.uuid4()),
            'reason': 'استقبال وتجربة ضيافة',
            'owner_treatment': 'draw_proposed' if category == 'owner_personal' else '',
            **kwargs,
        })

    def test_partial_line_hospitality_preserves_paid_remainder_and_cost_snapshot(self):
        response = self.post(user=self.manager)
        self.assertEqual(response.status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.subtotal_syp, 330)
        self.assertEqual(self.order.discount_syp, 90)
        self.assertEqual(self.order.total_syp, 240)
        self.assertEqual(self.order.remaining_syp, 240)
        record = OrderConsumption.objects.get()
        self.assertEqual(record.quantity, 1)
        self.assertEqual(record.category, 'hub_hospitality')
        self.assertEqual(record.retail_value_syp, 90)
        self.assertEqual(record.estimated_cost_syp, Decimal('32.00'))
        self.assertEqual(record.discount.discount_type, OrderDiscount.DiscountType.COMP)
        self.assertEqual(record.approved_by, self.manager)
        self.assertEqual(AuditEvent.objects.filter(action='pos_consumption_recorded').count(), 1)
        self.assertEqual(ActivityLog.objects.filter(action='pos_consumption_recorded').count(), 1)
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(CashMovement.objects.exists())
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(PostingBatch.objects.exists())

    def test_owner_personal_and_hospitality_can_coexist_on_one_order(self):
        self.assertEqual(self.post(user=self.owner, category='owner_personal').status_code, 302)
        self.assertEqual(self.post(user=self.owner, category='hub_hospitality').status_code, 302)
        self.assertEqual(OrderConsumption.objects.count(), 2)
        self.assertEqual(self.order.discount_syp, 180)
        self.assertEqual(self.order.remaining_syp, 150)
        personal = OrderConsumption.objects.get(category='owner_personal')
        self.assertEqual(personal.owner_treatment, 'draw_proposed')
        self.assertEqual(personal.estimated_cost_syp, Decimal('32.00'))
        self.assertFalse(Payment.objects.exists())

    def test_idempotent_request_key_and_disallow_duplicate_free_quantity(self):
        key = uuid.uuid4()
        self.post(key=key)
        self.post(key=key)
        self.assertEqual(OrderConsumption.objects.count(), 1)
        self.assertEqual(OrderDiscount.objects.count(), 1)
        self.post(qty=2)
        self.assertEqual(OrderConsumption.objects.count(), 1)
        self.assertEqual(self.order.total_syp, 240)

    def test_cashier_cannot_approve_and_manager_cannot_take_owner_personal_use(self):
        self.post(user=self.cashier)
        self.assertFalse(OrderConsumption.objects.exists())
        self.post(user=self.manager, category='owner_personal')
        self.assertFalse(OrderConsumption.objects.exists())
        self.post(user=self.manager)
        self.assertEqual(OrderConsumption.objects.count(), 1)
        cashier_get = self.client.get(reverse('staff_cashier_order', args=[self.order.public_code]))
        self.assertEqual(cashier_get.status_code, 200)
        self.assertNotContains(cashier_get, 'name="category" value="owner_personal"')

    def test_after_payment_or_other_discounts_refuse_comp_without_side_effects(self):
        Payment.objects.create(order=self.order, method=Payment.Method.CASH, amount_syp=10)
        self.post()
        self.assertFalse(OrderConsumption.objects.exists())
        Payment.objects.all().delete()
        OrderDiscount.objects.create(
            order=self.order, discount_type='member', amount_syp=10,
            reason='خصم عضوية',
        )
        self.post()
        self.assertFalse(OrderConsumption.objects.exists())
        self.assertEqual(OrderDiscount.objects.count(), 1)

    def test_closed_visit_and_cancelled_order_are_rejected(self):
        visit = HubVisit.objects.create(created_by=self.manager)
        self.order.visit = visit
        self.order.save(update_fields=['visit'])
        visit.status = HubVisit.Status.CLOSED
        visit.save(update_fields=['status'])
        self.post()
        self.assertFalse(OrderConsumption.objects.exists())
        self.order.visit = None
        self.order.status = Order.Status.CANCELLED
        self.order.save(update_fields=['visit', 'status'])
        self.post()
        self.assertFalse(OrderConsumption.objects.exists())

    def test_non_discountable_service_and_ineligible_product_types_rejected(self):
        line = OrderItem.objects.create(
            order=self.order, product=self.internet, quantity=1,
            product_name_ar_snapshot=self.internet.name_ar,
            unit_price_syp_snapshot=120, line_total_syp_snapshot=120,
        )
        self.post(line=line)
        self.assertFalse(OrderConsumption.objects.exists())

    def test_legacy_generic_comp_redirects_without_unclassified_discount(self):
        response = self.client.post(reverse('staff_cashier_discount', args=[self.order.public_code]), {
            'discount_type': 'comp', 'amount_syp': '90', 'reason': 'old unsourced comp',
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(OrderDiscount.objects.exists())

    def test_existing_consumption_blocks_legacy_order_item_edits(self):
        self.post()
        response = self.client.post(reverse('staff_order_edit_update_item',
                                         args=[self.order.public_code, self.coffee_line.pk]), {
            'quantity': '4', 'item_note': 'rewrite',
        })
        self.assertEqual(response.status_code, 302)
        self.coffee_line.refresh_from_db()
        self.assertEqual(self.coffee_line.quantity, 2)

    def test_report_owner_only_and_informational_totals(self):
        self.post(category='owner_personal', owner_treatment='reimburse_proposed')
        url = reverse('staff_consumption_report')
        result = self.client.get(url)
        self.assertEqual(result.status_code, 200)
        self.assertContains(result, 'الضيافة والاستهلاك الشخصي')
        self.assertContains(result, 'reimburse_proposed', count=0)
        self.assertContains(result, 'تعويض لاحق')
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.cashier)
        self.assertNotEqual(self.client.get(url).status_code, 200)

    def test_view_as_blocks_writing_even_for_real_superuser(self):
        session = self.client.session
        session[VIEW_AS_USER_SESSION_KEY] = self.cashier.pk
        session.save()
        response = self.post()
        self.assertEqual(response.status_code, 403)
        self.assertFalse(OrderDiscount.objects.exists())
        self.assertFalse(OrderConsumption.objects.exists())

    def test_zero_price_unrepresentable_per_unit_and_invalid_uuid_rejected(self):
        self.coffee_line.line_total_syp_snapshot = 181
        self.coffee_line.save(update_fields=['line_total_syp_snapshot'])
        self.post()
        self.assertFalse(OrderConsumption.objects.exists())
        self.coffee_line.line_total_syp_snapshot = 180
        self.coffee_line.save(update_fields=['line_total_syp_snapshot'])
        self.post(key='invalid-key')
        self.assertFalse(OrderConsumption.objects.exists())
