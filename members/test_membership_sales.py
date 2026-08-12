from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from core.models import ActivityLog, FinancialAccount, Member, Order, OrderItem, Payment
from members.membership_sales import create_membership_sale
from members.models import MembershipPlan, MembershipSubscription


class MembershipSaleTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(
            username='membership-cashier', phone='0911000001', password='x', role='cashier')
        self.member = Member.objects.create(name_ar='عضو', phone='0911000002')
        self.plan = MembershipPlan.objects.create(
            code='hub-member-sale', name_ar='عضو هَب', name_en='Hub Member',
            billing_period=MembershipPlan.BillingPeriod.MONTHLY, price_syp=50_000)
        FinancialAccount.objects.create(
            code='cash:membership', name_ar='صندوق العضويات', account_type='asset',
            scope='cashbox', is_active=True, negative_balance_policy='allow')
        FinancialAccount.objects.create(
            code='revenue:membership', name_ar='إيراد العضويات', account_type='revenue',
            scope='operating', is_active=True, negative_balance_policy='allow')

    def sale(self, key='membership-1', **kwargs):
        values = dict(member=self.member, plan=self.plan, payment_method=Payment.Method.CASH,
                      actor=self.actor, idempotency_key=key, starts_at=timezone.now())
        values.update(kwargs)
        return create_membership_sale(**values)

    def test_paid_sale_commits_exact_snapshots_before_activation(self):
        with self.captureOnCommitCallbacks(execute=True):
            result = self.sale()
        sub = result.subscription
        sub.refresh_from_db()
        self.assertEqual((Order.objects.count(), OrderItem.objects.count(), Payment.objects.count()), (1, 1, 1))
        self.assertEqual(sub.gross_amount_syp, 50_000)
        self.assertEqual(sub.order.items.get().unit_price_syp_snapshot, 50_000)
        self.assertEqual(sub.order.items.get().line_total_syp_snapshot, 50_000)
        self.assertEqual(sub.payment.amount_syp, 50_000)
        self.assertEqual(sub.order.total_syp, 50_000)
        self.assertEqual(sub.status, MembershipSubscription.Status.ACTIVE)
        self.assertTrue(ActivityLog.objects.filter(action='membership.sale_created').exists())

    @patch('members.models.MembershipSubscription.activate')
    def test_retry_is_idempotent_and_payload_conflict_is_rejected(self, activate):
        started = timezone.now()
        with self.captureOnCommitCallbacks(execute=True):
            first = self.sale(starts_at=started)
        second = self.sale(starts_at=started)
        self.assertEqual(first.subscription.pk, second.subscription.pk)
        self.assertEqual((Order.objects.count(), OrderItem.objects.count(), Payment.objects.count(),
                          MembershipSubscription.objects.count()), (1, 1, 1, 1))
        with self.assertRaises(ValidationError):
            self.sale(starts_at=started + timedelta(minutes=1))

    @patch('members.models.MembershipSubscription.activate')
    def test_price_changes_do_not_rewrite_history(self, activate):
        started = timezone.now()
        with self.captureOnCommitCallbacks(execute=True):
            old = self.sale('old-price', starts_at=started).subscription
        self.plan.price_syp = 60_000
        self.plan.save(update_fields=['price_syp', 'updated_at'])
        with self.captureOnCommitCallbacks(execute=True):
            new = self.sale('new-price', starts_at=started).subscription
        old.refresh_from_db()
        self.assertEqual((old.gross_amount_syp, old.payment.amount_syp,
                          old.order.items.get().line_total_syp_snapshot), (50_000, 50_000, 50_000))
        self.assertEqual((new.gross_amount_syp, new.payment.amount_syp), (60_000, 60_000))

    @patch('members.models.MembershipSubscription.activate')
    def test_future_sale_stays_pending_without_activation(self, activate):
        result = self.sale(starts_at=timezone.now() + timedelta(days=1))
        self.assertEqual(result.subscription.status, MembershipSubscription.Status.PENDING)
        activate.assert_not_called()

    def test_invalid_inactive_partial_and_unpaid_sales_write_nothing(self):
        cases = [
            {'ends_at': timezone.now() - timedelta(days=1)},
            {'payment_amount_syp': 1},
            {'payment_method': Payment.Method.UNPAID},
        ]
        for index, values in enumerate(cases):
            with self.assertRaises(ValidationError):
                self.sale(f'invalid-{index}', **values)
        self.plan.is_active = False
        self.plan.save(update_fields=['is_active', 'updated_at'])
        with self.assertRaises(ValidationError):
            self.sale('inactive')
        self.assertEqual((Order.objects.count(), Payment.objects.count(),
                          MembershipSubscription.objects.count()), (0, 0, 0))

    @patch('members.models.MembershipSubscription.activate')
    def test_free_sale_has_no_fake_payment(self, activate):
        self.plan.price_syp = 0
        self.plan.save(update_fields=['price_syp', 'updated_at'])
        with self.captureOnCommitCallbacks(execute=True):
            sub = self.sale(payment_method=None).subscription
        self.assertEqual(sub.order.total_syp, 0)
        self.assertEqual(sub.gross_amount_syp, 0)
        self.assertTrue(sub.is_complimentary)
        self.assertIsNone(sub.payment)
        self.assertEqual(Payment.objects.count(), 0)

    @patch('members.models.MembershipSubscription.activate', side_effect=RuntimeError('router unavailable'))
    def test_post_commit_activation_failure_preserves_sale_and_is_observable(self, activate):
        with self.captureOnCommitCallbacks(execute=True):
            sub = self.sale('activation-failure').subscription
        sub.refresh_from_db()
        self.assertEqual(sub.status, MembershipSubscription.Status.PENDING)
        self.assertIn('router unavailable', sub.activation_error)
        self.assertTrue(sub.order_id and sub.payment_id)
        self.assertTrue(ActivityLog.objects.filter(action='membership.sale_activation_failed').exists())

    def test_staff_route_permissions_get_and_csrf(self):
        url = reverse('staff_member_subscribe', args=[self.member.public_code])
        anonymous = Client()
        self.assertNotEqual(anonymous.post(url, {}).status_code, 200)
        self.client.force_login(self.actor)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'طريقة الدفع')
        self.assertEqual(MembershipSubscription.objects.count(), 0)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.actor)
        self.assertEqual(csrf_client.post(url, {}).status_code, 403)

    @patch('members.models.MembershipSubscription.activate')
    def test_authorized_staff_post_uses_commercial_checkout(self, activate):
        url = reverse('staff_member_subscribe', args=[self.member.public_code])
        self.client.force_login(self.actor)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, {
                'plan': self.plan.pk, 'payment_method': Payment.Method.CASH,
                'idempotency_key': 'staff-form-sale',
            })
        self.assertEqual(response.status_code, 302)
        subscription = MembershipSubscription.objects.get()
        self.assertTrue(subscription.order_id and subscription.payment_id)
        self.assertEqual(subscription.payment.amount_syp, 50_000)


@skipUnless(connection.vendor == 'postgresql', 'PostgreSQL locking semantics required')
class MembershipSalePostgresConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.actor = get_user_model().objects.create_user(
            username='concurrent-cashier', phone='0922000001', password='x', role='cashier')
        self.member = Member.objects.create(name_ar='عضو متزامن', phone='0922000002')
        self.plan = MembershipPlan.objects.create(code='concurrent-plan', name_ar='خطة', price_syp=50_000)
        FinancialAccount.objects.create(code='cash:concurrent-membership', name_ar='صندوق',
            account_type='asset', scope='cashbox', is_active=True, negative_balance_policy='allow')
        FinancialAccount.objects.create(code='revenue:concurrent-membership', name_ar='إيراد',
            account_type='revenue', scope='operating', is_active=True, negative_balance_policy='allow')

    @patch('members.models.MembershipSubscription.activate')
    def test_simultaneous_same_key_creates_one_sale(self, activate):
        started = timezone.now() + timedelta(days=1)

        def checkout():
            close_old_connections()
            try:
                return create_membership_sale(
                    member=Member.objects.get(pk=self.member.pk),
                    plan=MembershipPlan.objects.get(pk=self.plan.pk), payment_method=Payment.Method.CASH,
                    actor=get_user_model().objects.get(pk=self.actor.pk),
                    idempotency_key='same-concurrent-key', starts_at=started).subscription.pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            ids = list(executor.map(lambda _: checkout(), range(2)))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual((MembershipSubscription.objects.count(), Order.objects.count(),
                          OrderItem.objects.count(), Payment.objects.count()), (1, 1, 1, 1))
