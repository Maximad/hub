from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import (
    ActivityLog, Category, HubVisit, Order, OrderDiscount, OrderItem,
    Payment, Product, Room, SystemSetting, TableArea,
)
from core.settings_helpers import get_system_settings


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class UnpostedBillSplittingTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='split-admin', password='x', phone='985011', role='admin')
        self.waiter = User.objects.create_user(
            username='split-waiter', password='x', phone='985012', role='waiter')
        self.room = Room.objects.create(name_ar='الصالة')
        self.table_a = TableArea.objects.create(room=self.room, name_ar='طاولة أ')
        self.table_b = TableArea.objects.create(room=self.room, name_ar='طاولة ب')
        self.source = HubVisit.objects.create(table=self.table_a)
        self.destination = HubVisit.objects.create(table=self.table_b)
        self.product = Product.objects.create(
            category=Category.objects.create(name_ar='أصناف'),
            name_ar='مشروب تجربة', price_syp=120)
        self.order = Order.objects.create(
            visit=self.source, table=self.table_a,
            status=Order.Status.NEW,
            fulfillment_mode=Order.FulfillmentMode.TABLE,
            service_mode=Order.ServiceMode.TABLE,
        )
        self.item = OrderItem.objects.create(
            order=self.order, product=self.product, quantity=3,
            product_name_ar_snapshot='مشروب تجربة', unit_price_syp_snapshot=120,
            line_total_syp_snapshot=360,
            estimated_unit_cost_syp_snapshot=30,
            estimated_line_cost_syp_snapshot=90,
            estimated_line_margin_syp_snapshot=270,
            selected_options_snapshot=[{'group_name_ar': 'حجم', 'options': [{'name_ar': 'كبير'}]}],
            item_note='بدون ثلج', prep_status=OrderItem.PrepStatus.NEW,
        )
        self.settings = SystemSetting.objects.create(auto_deduct_inventory_on_sale=False)
        get_system_settings.cache_clear()
        self.client.force_login(self.admin)
        self.url = reverse('staff_visit_detail', kwargs={'public_code': self.source.public_code})

    def tearDown(self):
        get_system_settings.cache_clear()

    def payload(self, **kwargs):
        data = {
            'action': 'split_item',
            'order_id': self.order.pk,
            'item_id': self.item.pk,
            'quantity': '1',
            'destination_visit': str(self.destination.public_code),
            'reason': 'تقسيم حساب زبونين',
        }
        data.update(kwargs)
        return data

    def unchanged(self):
        self.item.refresh_from_db()
        self.assertEqual(self.item.order_id, self.order.pk)
        self.assertEqual(self.item.quantity, 3)
        self.assertFalse(ActivityLog.objects.filter(action='visit.order_item_split').exists())
        self.assertEqual(Order.objects.count(), 1)

    def test_drawer_exposes_split_form_without_second_page(self):
        page = self.client.get(self.url, {'panel': '1'})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'name="action" value="split_item"')
        self.assertContains(page, 'name="destination_visit"')
        self.assertContains(page, 'name="quantity"')
        self.assertContains(page, 'hx-target="#staff-context-drawer-body"')

    def test_partial_split_keeps_unit_snapshots_margin_and_total_balanced(self):
        before = self.source.remaining_syp
        response = self.client.post(self.url + '?panel=1', self.payload(), HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'staff/_visit_panel.html')
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 2)
        self.assertEqual(self.item.line_total_syp_snapshot, 240)
        self.assertEqual(self.item.estimated_line_cost_syp_snapshot, 60)
        self.assertEqual(self.item.estimated_line_margin_syp_snapshot, 180)
        self.assertEqual(Order.objects.count(), 2)
        new = Order.objects.exclude(pk=self.order.pk).get()
        self.assertEqual(new.visit_id, self.destination.pk)
        self.assertEqual(new.table_id, self.table_b.pk)
        self.assertEqual(new.status, Order.Status.NEW)
        moved = new.items.get()
        self.assertEqual(moved.quantity, 1)
        self.assertEqual(moved.product_name_ar_snapshot, self.item.product_name_ar_snapshot)
        self.assertEqual(moved.unit_price_syp_snapshot, 120)
        self.assertEqual(moved.selected_options_snapshot, self.item.selected_options_snapshot)
        self.assertEqual(moved.item_note, 'بدون ثلج')
        self.assertEqual(moved.estimated_line_cost_syp_snapshot, 30)
        self.assertEqual(moved.estimated_line_margin_syp_snapshot, 90)
        self.assertEqual(self.source.remaining_syp, 240)
        self.assertEqual(self.destination.remaining_syp, 120)
        self.assertEqual(self.source.remaining_syp + self.destination.remaining_syp, before)
        self.assertEqual(new.payments.count(), 0)
        self.assertEqual(new.stock_movements.count(), 0)
        log = ActivityLog.objects.get(action='visit.order_item_split')
        self.assertEqual(log.details['moved_quantity'], 1)
        self.assertEqual(log.details['reason'], 'تقسيم حساب زبونين')

    def test_full_line_move_reuses_original_item_id_when_other_items_remain(self):
        other = OrderItem.objects.create(
            order=self.order, product=self.product, quantity=1,
            product_name_ar_snapshot='شاي', unit_price_syp_snapshot=40,
            line_total_syp_snapshot=40,
        )
        response = self.client.post(self.url, self.payload(quantity='3'))
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertNotEqual(self.item.order_id, self.order.pk)
        self.assertEqual(self.item.pk, OrderItem.objects.get(pk=self.item.pk).pk)
        self.assertTrue(OrderItem.objects.filter(pk=other.pk, order=self.order).exists())
        self.assertEqual(OrderItem.objects.count(), 2)

    def test_last_line_cannot_leave_empty_order(self):
        self.client.post(self.url, self.payload(quantity='3'))
        self.unchanged()

    def test_stale_replay_cannot_split_the_same_requested_quantity_twice(self):
        first = self.client.post(self.url, self.payload(quantity='2'))
        self.assertEqual(first.status_code, 302)
        count = Order.objects.count()
        second = self.client.post(self.url, self.payload(quantity='2'))
        self.assertEqual(second.status_code, 302)
        self.assertEqual(Order.objects.count(), count)
        self.assertEqual(ActivityLog.objects.filter(action='visit.order_item_split').count(), 1)

    def test_paid_and_reversed_payment_history_remain_blocked(self):
        payment = Payment.objects.create(
            order=self.order, amount_syp=120, method=Payment.Method.CASH,
            created_by=self.admin,
        )
        self.client.post(self.url, self.payload())
        self.unchanged()
        from django.utils import timezone
        Payment.objects.filter(pk=payment.pk).update(
            is_reversed=True, is_active=False, reversal_reason='other',
            reversed_at=timezone.now(), reversed_by=self.admin,
        )
        self.client.post(self.url, self.payload())
        self.unchanged()

    def test_discount_and_stock_posting_modes_are_blocked(self):
        OrderDiscount.objects.create(order=self.order, amount_syp=10, reason='خصم تجريبي')
        self.client.post(self.url, self.payload())
        self.unchanged()
        self.order.discounts.all().delete()
        self.settings.auto_deduct_inventory_on_sale = True
        self.settings.stock_deduction_mode = SystemSetting.StockDeductionMode.ON_ORDER_CREATED
        self.settings.save(update_fields=['auto_deduct_inventory_on_sale', 'stock_deduction_mode'])
        get_system_settings.cache_clear()
        self.client.post(self.url, self.payload())
        self.unchanged()
        self.settings.auto_deduct_inventory_on_sale = False
        self.settings.stock_deduction_mode = SystemSetting.StockDeductionMode.DISABLED
        self.settings.save(update_fields=['auto_deduct_inventory_on_sale','stock_deduction_mode'])
        get_system_settings.cache_clear()
        self.item.stock_deducted = True
        self.item.save(update_fields=['stock_deducted'])
        self.client.post(self.url, self.payload())
        self.unchanged()

    def test_prep_state_delivery_and_closed_destination_are_blocked(self):
        self.item.prep_status = OrderItem.PrepStatus.PREPARING
        self.item.save(update_fields=['prep_status'])
        self.client.post(self.url, self.payload())
        self.unchanged()
        self.item.prep_status = OrderItem.PrepStatus.NEW
        self.item.save(update_fields=['prep_status'])
        self.order.fulfillment_mode = Order.FulfillmentMode.DELIVERY
        self.order.save(update_fields=['fulfillment_mode'])
        self.client.post(self.url, self.payload())
        self.unchanged()
        self.order.fulfillment_mode = Order.FulfillmentMode.TABLE
        self.order.save(update_fields=['fulfillment_mode'])
        self.destination.status = HubVisit.Status.CLOSED
        self.destination.save(update_fields=['status'])
        self.client.post(self.url, self.payload())
        self.unchanged()

    def test_invalid_parameters_and_same_visit_blocked(self):
        for change in (
            {'quantity': '0'}, {'quantity': '-3'}, {'quantity': '4'},
            {'quantity': 'abc'}, {'reason': ''}, {'reason': 'a'},
            {'order_id': 'none'}, {'item_id': 'none'},
            {'destination_visit': str(self.source.public_code)},
        ):
            with self.subTest(change=change):
                result = self.client.post(self.url, self.payload(**change))
                self.assertEqual(result.status_code, 302)
                self.unchanged()

    def test_waiter_without_visit_and_cashier_cannot_split(self):
        self.client.force_login(self.waiter)
        result = self.client.post(self.url, self.payload())
        self.assertEqual(result.status_code, 403)
        self.unchanged()

    def test_null_historical_cost_snapshots_remain_null_after_split(self):
        OrderItem.objects.filter(pk=self.item.pk).update(
            estimated_unit_cost_syp_snapshot=None,
            estimated_line_cost_syp_snapshot=None,
            estimated_line_margin_syp_snapshot=None,
        )
        self.client.post(self.url, self.payload())
        moved = Order.objects.exclude(pk=self.order.pk).get().items.get()
        self.assertIsNone(moved.estimated_unit_cost_syp_snapshot)
        self.assertIsNone(moved.estimated_line_cost_syp_snapshot)
        self.assertIsNone(moved.estimated_line_margin_syp_snapshot)
