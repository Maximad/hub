from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import (
    ActivityLog, Category, HubVisit, Order, OrderDiscount, OrderItem,
    Payment, Product, Room, TableArea,
)
from core.services.visit_order_corrections import move_unpaid_order
from django.core.exceptions import ValidationError


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class VisitOrderCorrectionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='visit-correction-admin', phone='986011', password='x', role='admin',
        )
        self.waiter = User.objects.create_user(
            username='visit-correction-waiter', phone='986012', password='x', role='waiter',
        )
        room = Room.objects.create(name_ar='تصحيح')
        self.table_a = TableArea.objects.create(room=room, name_ar='طاولة أ')
        self.table_b = TableArea.objects.create(room=room, name_ar='طاولة ب')
        self.source = HubVisit.objects.create(table=self.table_a)
        self.destination = HubVisit.objects.create(table=self.table_b)
        category = Category.objects.create(name_ar='التصحيحات')
        product = Product.objects.create(category=category, name_ar='شاي', price_syp=100)
        self.order = Order.objects.create(
            visit=self.source, table=self.table_a,
            fulfillment_mode=Order.FulfillmentMode.TABLE, status=Order.Status.NEW,
        )
        self.item = OrderItem.objects.create(
            order=self.order, product=product, product_name_ar_snapshot='شاي',
            unit_price_syp_snapshot=100, line_total_syp_snapshot=100, quantity=1,
            prep_status=OrderItem.PrepStatus.NEW,
        )
        self.client.force_login(self.admin)
        self.url = reverse('staff_visit_detail', kwargs={'public_code': self.source.public_code})

    def payload(self, **updates):
        data = {
            'action': 'transfer_order',
            'order_id': str(self.order.pk),
            'destination_visit': str(self.destination.public_code),
            'reason': 'تصحيح حساب الطاولة',
        }
        data.update(updates)
        return data

    def unchanged(self):
        self.order.refresh_from_db()
        self.assertEqual(self.order.visit_id, self.source.pk)
        self.assertEqual(self.order.table_id, self.table_a.pk)
        self.assertFalse(ActivityLog.objects.filter(action='visit.order_transferred').exists())

    def test_drawer_has_note_editor_and_whole_order_transfer_with_url_fallback(self):
        response = self.client.get(self.url, {'panel': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="action" value="edit_notes"')
        self.assertContains(response, 'name="action" value="transfer_order"')
        self.assertContains(response, f'value="{self.destination.public_code}"')
        self.assertContains(response, 'hx-target="#staff-context-drawer-body"')

    def test_move_unpaid_order_updates_both_bills_and_audits_once(self):
        before_source = self.source.remaining_syp
        response = self.client.post(
            self.url + '?panel=1', self.payload(), HTTP_HX_REQUEST='true',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'staff/_visit_panel.html')
        self.order.refresh_from_db()
        self.assertEqual(self.order.visit_id, self.destination.pk)
        self.assertEqual(self.order.table_id, self.table_b.pk)
        self.source.refresh_from_db()
        self.destination.refresh_from_db()
        self.assertGreater(before_source, 0)
        self.assertEqual(self.source.remaining_syp, 0)
        self.assertEqual(self.destination.remaining_syp, before_source)
        self.assertFalse(Payment.objects.filter(order=self.order).exists())
        entry = ActivityLog.objects.get(action='visit.order_transferred')
        self.assertEqual(entry.details['source_visit_id'], self.source.pk)
        self.assertEqual(entry.details['destination_visit_id'], self.destination.pk)
        self.assertEqual(entry.details['reason'], 'تصحيح حساب الطاولة')

    def test_stale_replay_and_wrong_source_do_not_move_order_twice(self):
        self.client.post(self.url, self.payload())
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ActivityLog.objects.filter(action='visit.order_transferred').count(), 1)
        self.order.refresh_from_db()
        self.assertEqual(self.order.visit_id, self.destination.pk)

    def test_rejects_paid_and_even_reversed_payments(self):
        payment = Payment.objects.create(
            order=self.order, amount_syp=20,
            method=Payment.Method.CASH, created_by=self.admin,
        )
        response = self.client.post(self.url + '?panel=1', self.payload())
        self.assertContains(response, 'سجل دفعات')
        self.unchanged()
        # Construct an already-reversed historical record directly in the
        # fixture; the production reversal API has its own dedicated tests.
        # Saving Payment here would trigger a separate legacy Internet-share
        # signal, unrelated to a visit-transfer safety check.
        from django.utils import timezone
        Payment.objects.filter(pk=payment.pk).update(
            is_reversed=True, is_active=False, reversal_reason='other',
            reversed_at=timezone.now(), reversed_by=self.admin,
        )
        self.client.post(self.url, self.payload())
        self.unchanged()

    def test_rejects_discount_and_preparation_in_progress(self):
        OrderDiscount.objects.create(
            order=self.order, amount_syp=10, reason='اختبار',
            discount_type=OrderDiscount.DiscountType.FIXED,
        )
        self.client.post(self.url, self.payload())
        self.unchanged()
        self.order.discounts.all().delete()
        self.item.prep_status = OrderItem.PrepStatus.PREPARING
        self.item.save(update_fields=['prep_status'])
        self.client.post(self.url, self.payload())
        self.unchanged()

    def test_rejects_closed_destination_and_unapproved_actor(self):
        self.destination.status = HubVisit.Status.CLOSED
        self.destination.save(update_fields=['status'])
        self.client.post(self.url, self.payload())
        self.unchanged()
        self.destination.status = HubVisit.Status.OPEN
        self.destination.save(update_fields=['status'])
        self.client.force_login(self.waiter)
        denied = self.client.post(self.url, self.payload())
        self.assertEqual(denied.status_code, 403)
        self.unchanged()

    def test_rejects_invalid_target_reason_and_same_destination(self):
        for change in (
            {'reason': ''},
            {'reason': 'ا'},
            {'destination_visit': '00000000-0000-0000-0000-000000000000'},
            {'destination_visit': str(self.source.public_code)},
            {'order_id': 'no'},
        ):
            with self.subTest(change=change):
                self.client.post(self.url, self.payload(**change))
                self.unchanged()

    def test_legacy_attach_and_detach_routes_obey_financial_guards(self):
        unattached = Order.objects.create(
            table=self.table_a, fulfillment_mode=Order.FulfillmentMode.TABLE,
            status=Order.Status.NEW,
        )
        Payment.objects.create(
            order=unattached, amount_syp=1,
            method=Payment.Method.CASH, created_by=self.admin,
        )
        self.client.post(self.url, {
            'action': 'attach_order', 'order_code': str(unattached.public_code),
        })
        unattached.refresh_from_db()
        self.assertIsNone(unattached.visit_id)
        self.client.post(self.url, {
            'action': 'detach_order', 'order_id': str(self.order.pk),
        })
        self.order.refresh_from_db()
        self.assertIsNone(self.order.visit_id)
        self.assertTrue(ActivityLog.objects.filter(action='visit.order_transferred').exists())

    def test_note_edit_is_in_place_audited_and_rejected_when_closed(self):
        response = self.client.post(
            self.url + '?panel=1',
            {'action': 'edit_notes', 'notes': 'تعديل ملاحظة الجلسة'},
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(response.status_code, 200)
        self.source.refresh_from_db()
        self.assertEqual(self.source.notes, 'تعديل ملاحظة الجلسة')
        self.assertEqual(ActivityLog.objects.filter(action='visit.notes_updated').count(), 1)
        self.client.post(self.url, {'action': 'edit_notes', 'notes': 'تعديل ملاحظة الجلسة'})
        self.assertEqual(ActivityLog.objects.filter(action='visit.notes_updated').count(), 1)
        self.source.status = HubVisit.Status.CLOSED
        self.source.save(update_fields=['status'])
        self.client.post(self.url, {'action': 'edit_notes', 'notes': 'لا تعدّل'})
        self.source.refresh_from_db()
        self.assertEqual(self.source.notes, 'تعديل ملاحظة الجلسة')

    def test_server_service_also_rejects_wrong_source_without_http(self):
        with self.assertRaises(ValidationError):
            move_unpaid_order(
                actor=self.admin, order_id=self.order.pk,
                source_visit_id=self.destination.pk,
                destination_visit_id=self.source.pk,
                reason='خطأ حساب',
            )
        self.unchanged()
