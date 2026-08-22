from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    ActivityLog,
    CashMovement,
    Category,
    FinancialAccount,
    HubVisit,
    InternetSession,
    Order,
    OrderItem,
    Payment,
    PostingCommand,
    Product,
    Room,
    SystemSetting,
    TableArea,
)
from core.settings_helpers import get_system_settings


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class VisitCashierSettlementTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='visit-cashier-admin', password='adminpass', phone='+96319101', role='admin'
        )
        self.cashier = User.objects.create_user(
            username='visit-cashier-user', password='cashierpass', phone='+96319102', role='cashier'
        )
        self.cash = FinancialAccount.objects.create(
            code='cash:visit-settlement', name_ar='صندوق الجلسات', account_type='asset',
            scope='cashbox', is_active=True, negative_balance_policy='allow',
        )
        self.revenue = FinancialAccount.objects.create(
            code='revenue:visit-settlement', name_ar='إيراد الجلسات', account_type='revenue',
            scope='operating', is_active=True, negative_balance_policy='allow',
        )
        self.room = Room.objects.create(name_ar='مشاريب')
        self.table = TableArea.objects.create(room=self.room, name_ar='طاولة الحساب')
        self.category = Category.objects.create(name_ar='اختبار الحساب')
        self.product = Product.objects.create(
            category=self.category, name_ar='صنف الحساب', price_syp=100,
            product_type=Product.ProductType.FOOD, item_type=Product.ItemType.FOOD,
        )
        self.internet_product = Product.objects.create(
            category=self.category, name_ar='إنترنت حسب الوقت', price_syp=0,
            product_type=Product.ProductType.INTERNET, item_type=Product.ItemType.SERVICE,
            service_type=Product.ServiceType.INTERNET, requires_preparation=False,
            visible_on_qr=False, orderable_on_qr=False, visible_on_pos=False,
            orderable_on_pos=False, not_discountable=True, track_margin=False,
        )
        SystemSetting.objects.create(
            internet_metered_enabled=True,
            auto_create_order_for_metered_sessions=True,
            internet_service_product=self.internet_product,
            default_rate_per_hour_syp=600,
            default_minimum_minutes=30,
            default_rounding_increment_minutes=15,
        )
        get_system_settings.cache_clear()
        self.client.force_login(self.admin)

    def tearDown(self):
        get_system_settings.cache_clear()

    def visit(self):
        return HubVisit.objects.create(table=self.table, created_by=self.admin)

    def order(self, amount, *, visit=None, status=Order.Status.NEW, name='صنف الحساب'):
        order = Order.objects.create(
            table=self.table if visit else None,
            visit=visit,
            service_mode=Order.ServiceMode.TABLE if visit else Order.ServiceMode.DINE_IN,
            fulfillment_mode=Order.FulfillmentMode.TABLE if visit else Order.FulfillmentMode.INSIDE_SPACE,
            status=status,
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            quantity=1,
            product_name_ar_snapshot=name,
            unit_price_syp_snapshot=amount,
            line_total_syp_snapshot=amount,
            selected_options_snapshot=[],
            prep_status=OrderItem.PrepStatus.NO_PREP,
        )
        return order

    def payment_post(self, target, amount, *, key='visit-payment-test', user=None, **extra):
        if user is not None:
            self.client.force_login(user)
        payload = {'amount_syp': str(amount), 'method': Payment.Method.CASH, **extra}
        return self.client.post(
            reverse('staff_cashier_pay', kwargs={'public_code': target.public_code}),
            payload,
            HTTP_IDEMPOTENCY_KEY=key,
        )

    def test_cashier_groups_orders_by_open_visit_and_keeps_standalone_orders_separate(self):
        visit = self.visit()
        first = self.order(100, visit=visit, name='الأول')
        second = self.order(200, visit=visit, name='الثاني')
        standalone = self.order(70, name='منفرد')

        response = self.client.get(reverse('staff_cashier'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'staff/cashier.html')
        self.assertEqual(len(response.context['visit_rows']), 1)
        row = response.context['visit_rows'][0]
        self.assertEqual(row['visit'].pk, visit.pk)
        self.assertEqual([order.pk for order in row['orders']], [first.pk, second.pk])
        self.assertEqual((row['total'], row['paid'], row['remaining']), (300, 0, 300))
        self.assertEqual([row['order'].pk for row in response.context['standalone_rows']], [standalone.pk])
        self.assertContains(response, '2</strong> طلبات مرتبطة بهذه الجلسة', html=True)

    def test_order_cashier_link_resolves_to_combined_visit_account(self):
        visit = self.visit()
        first = self.order(100, visit=visit, name='الأول')
        second = self.order(200, visit=visit, name='الثاني')

        response = self.client.get(
            reverse('staff_cashier_order', kwargs={'public_code': first.public_code})
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'staff/cashier_visit.html')
        self.assertContains(response, first.display_number)
        self.assertContains(response, second.display_number)
        self.assertContains(response, '300')
        self.assertContains(response, 'الحساب المجمع')

    def test_numeric_order_search_redirects_to_visit_account(self):
        visit = self.visit()
        order = self.order(100, visit=visit)

        response = self.client.get(reverse('staff_cashier'), {'q': str(order.pk)})

        self.assertRedirects(
            response,
            reverse('staff_cashier_order', kwargs={'public_code': visit.public_code}),
        )

    def test_combined_payment_allocates_oldest_unpaid_order_first(self):
        visit = self.visit()
        first = self.order(100, visit=visit, name='الأقدم')
        second = self.order(200, visit=visit, name='الأحدث')

        response = self.payment_post(visit, 150, key='visit:oldest-first')

        self.assertEqual(response.status_code, 302)
        first.refresh_from_db(); second.refresh_from_db(); visit.refresh_from_db()
        self.assertEqual(first.paid_syp, 100)
        self.assertEqual(second.paid_syp, 50)
        self.assertEqual(visit.remaining_syp, 150)
        self.assertEqual(Payment.objects.filter(order=first, is_active=True).count(), 1)
        self.assertEqual(Payment.objects.filter(order=second, is_active=True).count(), 1)
        self.assertEqual(CashMovement.objects.filter(related_order=first, is_cancelled=False).count(), 1)
        self.assertEqual(CashMovement.objects.filter(related_order=second, is_cancelled=False).count(), 1)
        self.assertTrue(PostingCommand.objects.filter(key='visit:oldest-first', command='visit_payment.collect').exists())
        self.assertTrue(ActivityLog.objects.filter(action='visit_payment_allocated').exists())

    def test_duplicate_combined_payment_is_idempotent_and_does_not_spill_to_later_orders(self):
        visit = self.visit()
        first = self.order(100, visit=visit)
        second = self.order(200, visit=visit)

        self.payment_post(visit, 150, key='visit:duplicate')
        self.payment_post(visit, 150, key='visit:duplicate')

        first.refresh_from_db(); second.refresh_from_db(); visit.refresh_from_db()
        self.assertEqual((first.paid_syp, second.paid_syp, visit.remaining_syp), (100, 50, 150))
        self.assertEqual(Payment.objects.filter(order__visit=visit).count(), 2)
        self.assertEqual(sum(Payment.objects.filter(order__visit=visit).values_list('amount_syp', flat=True)), 150)
        self.assertEqual(PostingCommand.objects.filter(key='visit:duplicate').count(), 1)

    def test_partial_visit_payment_requires_manager_for_cashier(self):
        visit = self.visit()
        first = self.order(100, visit=visit)
        self.order(200, visit=visit)

        denied = self.payment_post(
            visit, 50, key='visit:partial-denied', user=self.cashier,
        )
        self.assertEqual(denied.status_code, 302)
        self.assertFalse(Payment.objects.filter(order__visit=visit).exists())

        approved = self.payment_post(
            visit,
            50,
            key='visit:partial-approved',
            user=self.cashier,
            manager_username=self.admin.username,
            manager_password='adminpass',
        )
        self.assertEqual(approved.status_code, 302)
        first.refresh_from_db(); visit.refresh_from_db()
        self.assertEqual(first.paid_syp, 50)
        self.assertEqual(visit.remaining_syp, 250)
        self.assertTrue(ActivityLog.objects.filter(action='visit_partial_payment_approved').exists())

    def test_cancelled_order_is_excluded_from_combined_account(self):
        visit = self.visit()
        active = self.order(100, visit=visit)
        cancelled = self.order(900, visit=visit, status=Order.Status.CANCELLED)

        response = self.client.get(
            reverse('staff_cashier_order', kwargs={'public_code': visit.public_code})
        )

        self.assertEqual(response.context['total'], 100)
        self.assertEqual([order.pk for order in response.context['orders']], [active.pk])
        self.assertNotContains(response, cancelled.display_number)

    def test_combined_receipts_include_all_visit_orders(self):
        visit = self.visit()
        first = self.order(100, visit=visit, name='طبق أول')
        second = self.order(200, visit=visit, name='طبق ثان')
        base = reverse('staff_cashier_order', kwargs={'public_code': visit.public_code})

        a4 = self.client.get(base + '?receipt=1')
        thermal = self.client.get(base + '?receipt=thermal')

        self.assertEqual(a4.status_code, 200)
        self.assertTemplateUsed(a4, 'staff/prints/visit_receipt.html')
        self.assertContains(a4, first.display_number)
        self.assertContains(a4, second.display_number)
        self.assertContains(a4, 'طبق أول')
        self.assertContains(a4, 'طبق ثان')
        self.assertEqual(thermal.status_code, 200)
        self.assertTemplateUsed(thermal, 'staff/prints/visit_receipt_thermal.html')
        self.assertContains(thermal, first.display_number)
        self.assertContains(thermal, second.display_number)

    def test_settle_and_close_pays_all_orders_and_closes_visit(self):
        visit = self.visit()
        first = self.order(100, visit=visit)
        second = self.order(200, visit=visit)

        response = self.client.post(
            reverse('staff_cashier_pay', kwargs={'public_code': visit.public_code}),
            {'action': 'settle_close', 'method': Payment.Method.CASH},
            HTTP_IDEMPOTENCY_KEY='visit:settle-close',
        )

        self.assertEqual(response.status_code, 302)
        visit.refresh_from_db(); first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual(visit.status, HubVisit.Status.CLOSED)
        self.assertIsNotNone(visit.closed_at)
        self.assertEqual(visit.remaining_syp, 0)
        self.assertEqual((first.paid_syp, second.paid_syp), (100, 200))
        self.assertTrue(ActivityLog.objects.filter(action='visit.settled_and_closed').exists())

    def test_settle_and_close_finalizes_metered_internet_before_collecting_final_balance(self):
        visit = self.visit()
        food_order = self.order(100, visit=visit)
        started = timezone.now() - timedelta(minutes=31)
        session = InternetSession.objects.create(
            session_type=InternetSession.SessionType.INTERNET,
            visit=visit,
            billing_mode=InternetSession.BillingMode.OPEN_METERED,
            start_time=started,
            started_at=started,
            rate_per_hour_syp=600,
            minimum_minutes=30,
            rounding_increment_minutes=15,
            status=InternetSession.Status.ACTIVE,
        )

        response = self.client.post(
            reverse('staff_cashier_pay', kwargs={'public_code': visit.public_code}),
            {'action': 'settle_close', 'method': Payment.Method.CASH},
            HTTP_IDEMPOTENCY_KEY='visit:settle-metered',
        )

        self.assertEqual(response.status_code, 302)
        visit.refresh_from_db(); session.refresh_from_db(); food_order.refresh_from_db()
        self.assertEqual(visit.status, HubVisit.Status.CLOSED)
        self.assertEqual(session.status, InternetSession.Status.BILLED)
        internet_order = Order.objects.get(visit=visit, items__product=self.internet_product)
        self.assertGreater(internet_order.total_syp, 0)
        self.assertEqual(food_order.remaining_syp, 0)
        self.assertEqual(internet_order.remaining_syp, 0)
        self.assertEqual(visit.remaining_syp, 0)

    def test_invalid_settle_rolls_back_metered_finalization_and_never_writes_off_balance(self):
        visit = self.visit()
        self.order(100, visit=visit)
        started = timezone.now() - timedelta(minutes=31)
        session = InternetSession.objects.create(
            session_type=InternetSession.SessionType.INTERNET,
            visit=visit,
            billing_mode=InternetSession.BillingMode.OPEN_METERED,
            start_time=started,
            started_at=started,
            rate_per_hour_syp=600,
            minimum_minutes=30,
            rounding_increment_minutes=15,
            status=InternetSession.Status.ACTIVE,
        )

        response = self.client.post(
            reverse('staff_cashier_pay', kwargs={'public_code': visit.public_code}),
            {'action': 'settle_close', 'method': ''},
            HTTP_IDEMPOTENCY_KEY='visit:settle-invalid',
        )

        self.assertEqual(response.status_code, 302)
        visit.refresh_from_db(); session.refresh_from_db()
        self.assertEqual(visit.status, HubVisit.Status.OPEN)
        self.assertGreater(visit.remaining_syp, 0)
        self.assertEqual(session.status, InternetSession.Status.ACTIVE)
        self.assertFalse(Order.objects.filter(visit=visit, items__product=self.internet_product).exists())

    def test_standalone_order_keeps_legacy_cashier_flow(self):
        standalone = self.order(70, name='منفرد')

        response = self.client.get(
            reverse('staff_cashier_order', kwargs={'public_code': standalone.public_code})
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'staff/cashier_order.html')
        self.assertNotContains(response, 'حساب مجمع')
