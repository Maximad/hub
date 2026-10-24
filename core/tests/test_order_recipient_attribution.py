"""Staff and public order provenance and descriptive recipient labels.

Recipient classification is deliberately not a discount or approved staff benefit.
"""
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    ActivityLog, Category, Order, OrderConsumption, OrderDiscount, OrderItem,
    Payment, Product,
)
from catalog.models import MenuSection


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False,
                   STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class OrderRecipientAttributionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.cashier = User.objects.create_user(
            username='recipient-cashier', phone='095960001', role='cashier')
        self.waiter = User.objects.create_user(
            username='recipient-waiter', phone='095960002', role='waiter')
        self.manager = User.objects.create_user(
            username='recipient-manager', phone='095960003', role='admin')
        category = Category.objects.create(name_ar='اختبار توثيق الطلب')
        section = MenuSection.objects.create(name_ar='قائمة اختبار التوثيق')
        self.drink = Product.objects.create(
            category=category, name_ar='شاي الاختبار', price_syp=200,
            is_available=True, visible_on_qr=True, orderable_on_qr=True,
            visible_on_pos=True, orderable_on_pos=True,
        )
        self.drink.menu_sections.add(section)
        self.client.force_login(self.cashier)

    def pos(self, *, recipient_kind='customer', recipient_note=''):
        return self.client.post(reverse('staff_pos'), {
            'fulfillment_mode': Order.FulfillmentMode.INSIDE_SPACE,
            f'qty_{self.drink.pk}': '1',
            'recipient_kind': recipient_kind,
            'recipient_note': recipient_note,
        })

    def test_staff_pos_stamps_actor_and_neighbor_at_full_price(self):
        result = self.pos(recipient_kind='neighbor', recipient_note='جار المبنى')
        self.assertEqual(result.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.entry_source, Order.EntrySource.STAFF_POS)
        self.assertEqual(order.entered_by, self.cashier)
        self.assertEqual(order.recipient_kind, Order.RecipientKind.NEIGHBOR)
        self.assertEqual(order.recipient_note, 'جار المبنى')
        self.assertEqual(order.total_syp, 200)
        self.assertEqual(order.discount_syp, 0)
        self.assertEqual(order.remaining_syp, 200)
        self.assertEqual(order.consumptions.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(ActivityLog.objects.filter(action='staff_pos_order_created').count(), 1)
        self.assertContains(self.client.get(reverse('staff_home')), 'أدخله')
        details = self.client.get(reverse('staff_order_edit', args=[order.public_code]))
        self.assertContains(details, 'جار هَبّ')
        self.assertContains(details, 'recipient_note')
        self.assertContains(details, self.cashier.username)

    def test_staff_label_is_not_hospitality_or_owner_personal(self):
        self.assertEqual(self.pos(recipient_kind='staff', recipient_note='فريق البار').status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.get_recipient_kind_display(), 'أحد أفراد الفريق')
        self.assertEqual(order.discount_syp, 0)
        self.assertEqual(OrderDiscount.objects.count(), 0)
        self.assertEqual(OrderConsumption.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)

    def test_recipient_correction_after_payment_does_not_change_money_or_actor(self):
        self.pos()
        order = Order.objects.get()
        Payment.objects.create(order=order, method=Payment.Method.CASH, amount_syp=100)
        before = (order.total_syp, order.paid_syp, order.remaining_syp)
        url = reverse('staff_order_recipient_update', args=[order.public_code])
        response = self.client.post(url, {'recipient_kind':'neighbor',
                                          'recipient_note':'صديق هَبّ'})
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual((order.total_syp, order.paid_syp, order.remaining_syp), before)
        self.assertEqual(order.entered_by_id, self.cashier.pk)
        self.assertEqual(order.entry_source, Order.EntrySource.STAFF_POS)
        self.assertEqual((order.recipient_kind, order.recipient_note),
                         ('neighbor', 'صديق هَبّ'))
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(OrderDiscount.objects.count(), 0)
        change = ActivityLog.objects.get(action='order.recipient_metadata_changed')
        self.assertEqual(change.actor, self.cashier)
        self.assertFalse(change.details['financial_change'])
        self.assertEqual(change.details['old']['kind'], 'customer')

    def test_invalid_recipient_inputs_do_not_change_order(self):
        self.pos()
        order = Order.objects.get()
        url = reverse('staff_order_recipient_update', args=[order.public_code])
        for data in [
            {'recipient_kind':'owner_personal', 'recipient_note':''},
            {'recipient_kind':'staff', 'recipient_note':'x'*121},
        ]:
            response = self.client.post(url, data)
            self.assertEqual(response.status_code, 302)
            order.refresh_from_db()
            self.assertEqual(order.recipient_kind, 'customer')
            self.assertEqual(order.recipient_note, '')
        self.assertFalse(ActivityLog.objects.filter(
            action='order.recipient_metadata_changed').exists())

    def test_waiter_and_anonymous_cannot_reclassify_an_order(self):
        self.pos()
        order = Order.objects.get()
        url = reverse('staff_order_recipient_update', args=[order.public_code])
        self.client.force_login(self.waiter)
        denied = self.client.post(url, {'recipient_kind':'staff','recipient_note':'test'})
        self.assertEqual(denied.status_code, 403)
        self.client.logout()
        logged_out = self.client.post(url, {'recipient_kind':'neighbor','recipient_note':'test'})
        self.assertIn(logged_out.status_code, (302, 403))
        order.refresh_from_db()
        self.assertEqual(order.recipient_kind, 'customer')

    def test_invalid_pos_label_fails_before_any_order_exists(self):
        response = self.pos(recipient_kind='owner_personal')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Order.objects.count(), 0)
        self.assertContains(response, 'نوع المستفيد غير صالح')

    def test_public_menu_order_is_recognized_as_customer_entry(self):
        self.client.logout()
        response = self.client.post(reverse('menu_public'), {
            'fulfillment_mode': Order.FulfillmentMode.INSIDE_SPACE,
            f'qty_{self.drink.pk}': '1',
        })
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.entry_source, Order.EntrySource.PUBLIC_MENU)
        self.assertIsNone(order.entered_by_id)
        self.assertEqual(order.recipient_kind, Order.RecipientKind.CUSTOMER)

    def test_existing_orders_keep_unknown_provenance_without_guessing(self):
        order = Order.objects.create()
        self.assertEqual(order.entry_source, Order.EntrySource.UNKNOWN)
        self.assertIsNone(order.entered_by_id)
        self.assertEqual(order.recipient_kind, Order.RecipientKind.CUSTOMER)
        page = self.client.get(reverse('staff_order_edit', args=[order.public_code]))
        self.assertContains(page, 'غير موثق')

    def test_classified_hospitality_is_displayed_separately_from_recipient(self):
        self.pos(recipient_kind='neighbor')
        order = Order.objects.get()
        item = OrderItem.objects.get(order=order)
        discount = OrderDiscount.objects.create(
            order=order, discount_type=OrderDiscount.DiscountType.COMP,
            amount_syp=200, reason='ضيافة مسجلة')
        OrderConsumption.objects.create(
            order=order, order_item=item, discount=discount,
            category=OrderConsumption.Category.HUB_HOSPITALITY,
            quantity=1, retail_value_syp=200,
            reason='ضيافة رسمية', beneficiary_note='ضيف',
            business_date=timezone.localdate(),
            request_key=str(uuid.uuid4()), created_by=self.manager,
            approved_by=self.manager,
        )
        page = self.client.get(reverse('staff_order_edit', args=[order.public_code]))
        self.assertContains(page, 'جار هَبّ')
        self.assertContains(page, 'ضيافة هَب')
        self.assertContains(page, 'الاستهلاك المصنّف والمسجّل')
