from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from threading import Barrier, Thread

from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (CashMovement, DailyClose, FinancialAccount, InternetAccessDevice, InternetBandwidthProfile, InternetEntitlement,
                         InternetPackage, InternetPartner, InternetPartnerUser, InternetRevenueShare,
                         InternetRevenueShareAdjustment, InternetSession, InternetUsageLedger, Member, Order, Payment,
                         PostingBatch, PostingCommand)
from core.services.internet_access import (create_entitlement, end_usage_session,
    create_commercial_sale, daily_minutes_remaining, daily_minutes_used,
    effectively_active_entitlements, get_default_internet_partner,
    record_payment_reversal_adjustment,
    register_device, start_usage_session, validity_end)
from core.services.network_backends import ManualNetworkBackend, get_network_backend
from core.services.posting.closing import close_totals
from core.services.posting.exceptions import ClosedPeriodError
from members.models import MembershipPlan, MembershipSubscription


class PackagePolicyTests(TestCase):
    def package(self, **overrides):
        values = dict(name_ar='باقة', duration_minutes=0, price_syp=100,
            code='package', access_mode=InternetPackage.AccessMode.TIMED_SESSION,
            session_minutes_limit=120)
        values.update(overrides)
        return InternetPackage(**values)

    def test_access_mode_validation(self):
        for mode, fields in (
            ('timed_session', {}), ('validity_pass', {}), ('allowance', {}),
            ('unlimited', {}), ('membership_credit', {}),
        ):
            package = self.package(code=mode, access_mode=mode, session_minutes_limit=None, **fields)
            with self.assertRaises(ValidationError): package.full_clean()

    def test_valid_policy_dimensions_are_database_configurable(self):
        profile = InternetBandwidthProfile.objects.create(code='fast', name='15 Mbps', download_limit_kbps=15000)
        package = self.package(access_mode='allowance', session_minutes_limit=None,
            total_minutes_limit=1200, validity_value=30, validity_unit='days',
            bandwidth_profile=profile, max_concurrent_devices=2, max_registered_devices=3)
        package.full_clean()

    def test_calendar_month_validity_is_deterministic(self):
        start = timezone.make_aware(timezone.datetime(2026, 1, 31, 10))
        self.assertEqual(validity_end(start, 1, 'months').day, 28)


class EntitlementWorkflowTests(TestCase):
    def setUp(self):
        self.timed = InternetPackage.objects.create(name_ar='ساعتان', code='two-hours', duration_minutes=120,
            price_syp=1000, access_mode='timed_session', session_minutes_limit=120)

    def test_guest_member_and_idempotent_voucher(self):
        first = create_entitlement(self.timed, guest_name='زائر', idempotency_key='sale-1')
        retry = create_entitlement(self.timed, guest_name='ignored', idempotency_key='sale-1')
        self.assertEqual(first.pk, retry.pk)
        self.assertRegex(first.access_code, r'^[^01OIL]{4}-[^01OIL]{4}$')
        member = Member.objects.create(name_ar='عضو', phone='0999999999')
        self.assertEqual(create_entitlement(self.timed, member=member).member, member)

    def test_activation_policies_and_weekly_unlimited(self):
        weekly = InternetPackage.objects.create(name_ar='أسبوعي', code='weekly', duration_minutes=0,
            price_syp=2000, access_mode='unlimited', activation_policy='on_first_use',
            validity_value=1, validity_unit='weeks')
        ent = create_entitlement(weekly)
        self.assertEqual(ent.status, 'pending')
        session = start_usage_session(ent)
        ent.refresh_from_db()
        self.assertEqual(ent.status, 'active')
        self.assertEqual(ent.valid_until - ent.valid_from, timedelta(days=7))
        end_usage_session(session, at=session.started_at + timedelta(minutes=25))
        ent.refresh_from_db(); self.assertEqual(ent.minutes_used, 0)

    def test_allowance_consumption_is_idempotent_and_nonnegative(self):
        package = InternetPackage.objects.create(name_ar='20 ساعة', code='20h', duration_minutes=0,
            price_syp=5000, access_mode='allowance', total_minutes_limit=1200,
            validity_value=30, validity_unit='days')
        ent = create_entitlement(package)
        session = start_usage_session(ent)
        end_usage_session(session, at=session.started_at + timedelta(minutes=75))
        end_usage_session(session, at=session.started_at + timedelta(minutes=100))
        ent.refresh_from_db()
        self.assertEqual(ent.minutes_used, 75)
        self.assertEqual(ent.minutes_remaining, 1125)

    def test_intersected_limits_reservation_settlement_and_overrun(self):
        package = InternetPackage.objects.create(name_ar='حدود', code='limits', duration_minutes=0,
            price_syp=1, access_mode='allowance', total_minutes_limit=90,
            daily_minutes_limit=40, session_minutes_limit=60, max_concurrent_devices=2)
        ent = create_entitlement(package)
        now = timezone.now().replace(second=0, microsecond=0)
        ent.valid_until = now + timedelta(minutes=25, seconds=30)
        ent.save(update_fields=['valid_until'])
        session = start_usage_session(ent, at=now)
        self.assertEqual((session.authorized_minutes, session.reserved_minutes), (25, 25))
        self.assertLessEqual(session.authorized_until, ent.valid_until)
        end_usage_session(session, at=now + timedelta(minutes=35))
        session.refresh_from_db(); ent.refresh_from_db()
        self.assertEqual((session.actual_duration_minutes, session.allowance_minutes_consumed,
                          session.overrun_minutes), (35, 25, 10))
        self.assertEqual(ent.minutes_used, 25)

    def test_daily_usage_resets_and_splits_at_local_midnight(self):
        package = InternetPackage.objects.create(name_ar='يومي', code='daily-split', duration_minutes=0,
            price_syp=1, access_mode='allowance', total_minutes_limit=100,
            daily_minutes_limit=60, session_minutes_limit=60)
        ent = create_entitlement(package)
        local_tz = timezone.get_current_timezone()
        start = timezone.make_aware(timezone.datetime(2026, 1, 2, 23, 58), local_tz)
        session = InternetSession.objects.create(entitlement=ent, package=package,
            start_time=start, started_at=start, billing_mode='prepaid', status='active')
        # A legacy nullable-authorization session remains endable, and its finalized
        # usage is allocated across the local date boundary rather than to one day.
        end_usage_session(session, at=start + timedelta(minutes=4))
        self.assertEqual(daily_minutes_used(ent, start.date()), 2)
        self.assertEqual(daily_minutes_used(ent, start.date() + timedelta(days=1)), 2)
        self.assertEqual(daily_minutes_remaining(ent, start + timedelta(days=1)), 58)

    def test_manual_activation_expiry_devices_and_backend(self):
        package = InternetPackage.objects.create(name_ar='يومي', code='day', duration_minutes=0,
            price_syp=500, access_mode='validity_pass', activation_policy='manual',
            validity_value=1, validity_unit='days', max_registered_devices=1)
        ent = create_entitlement(package)
        with self.assertRaises(ValidationError): start_usage_session(ent)
        from core.services.internet_access import activate_entitlement
        ent = activate_entitlement(ent)
        register_device(ent, 'AA:BB')
        with self.assertRaises(ValidationError): register_device(ent, 'CC:DD')
        backend = ManualNetworkBackend(); backend.provision_access(ent); backend.provision_access(ent)
        ent.refresh_from_db(); self.assertEqual(ent.network_status, 'provisioned')
        backend.disconnect_access(ent); ent.refresh_from_db(); self.assertEqual(ent.network_status, 'disconnected')
        self.assertIsInstance(get_network_backend(), ManualNetworkBackend)

    def test_membership_credit_has_no_duplicate_minute_ledger(self):
        member = Member.objects.create(name_ar='عضو', phone='0888888888')
        plan = MembershipPlan.objects.create(code='m', name_ar='عضوية')
        subscription = MembershipSubscription.objects.create(member=member, plan=plan,
            starts_at=timezone.now(), remaining_internet_minutes=90)
        package = InternetPackage.objects.create(name_ar='رصيد عضو', code='credit', duration_minutes=0,
            price_syp=0, access_mode='membership_credit', member_only=True, guest_allowed=False)
        ent = create_entitlement(package, member=member, subscription=subscription)
        self.assertIsNone(ent.total_minutes_allowed)
        self.assertEqual(subscription.remaining_internet_minutes, 90)

    def membership_credit(self, *, minutes=90, status='active', ends_at=None):
        member = Member.objects.create(name_ar='عضو الرصيد', phone=f'08{Member.objects.count():08d}')
        plan = MembershipPlan.objects.create(code=f'credit-{member.pk}', name_ar='عضوية')
        subscription = MembershipSubscription.objects.create(
            member=member, plan=plan, starts_at=timezone.now() - timedelta(days=1),
            ends_at=ends_at, status=status, remaining_internet_minutes=minutes)
        package = InternetPackage.objects.create(
            name_ar='رصيد عضو', code=f'credit-{member.pk}', duration_minutes=0,
            price_syp=0, access_mode='membership_credit', member_only=True,
            guest_allowed=False, max_concurrent_devices=2)
        return subscription, create_entitlement(package, member=member, subscription=subscription)

    def test_membership_session_longer_than_balance_caps_consumption(self):
        subscription, entitlement = self.membership_credit(minutes=17)
        session = start_usage_session(entitlement)

        end_usage_session(session, at=session.started_at + timedelta(minutes=40))

        session.refresh_from_db(); subscription.refresh_from_db()
        self.assertEqual(session.actual_duration_minutes, 40)
        self.assertEqual(session.allowance_minutes_consumed, 17)
        self.assertEqual(session.member_minutes_used, 17)
        self.assertEqual(subscription.remaining_internet_minutes, 0)

    def test_exhausted_and_expired_memberships_cannot_start(self):
        _, exhausted = self.membership_credit(minutes=0)
        _, expired = self.membership_credit(
            minutes=10, ends_at=timezone.now() - timedelta(seconds=1))

        with self.assertRaises(ValidationError):
            start_usage_session(exhausted)
        with self.assertRaises(ValidationError):
            start_usage_session(expired)

    def test_membership_end_retry_is_idempotent(self):
        subscription, entitlement = self.membership_credit(minutes=30)
        session = start_usage_session(entitlement)
        ended_at = session.started_at + timedelta(minutes=12)

        end_usage_session(session, at=ended_at)
        end_usage_session(session, at=ended_at + timedelta(minutes=10))

        session.refresh_from_db(); subscription.refresh_from_db()
        self.assertEqual(session.allowance_minutes_consumed, 12)
        self.assertEqual(subscription.remaining_internet_minutes, 18)


class MembershipCreditConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_simultaneous_starts_cannot_reserve_membership_balance_twice(self):
        member = Member.objects.create(name_ar='عضو متزامن', phone='0777777777')
        plan = MembershipPlan.objects.create(code='concurrent-credit', name_ar='عضوية')
        subscription = MembershipSubscription.objects.create(
            member=member, plan=plan, starts_at=timezone.now() - timedelta(days=1),
            status='active', remaining_internet_minutes=60)
        package = InternetPackage.objects.create(
            name_ar='رصيد متزامن', code='concurrent-credit', duration_minutes=0,
            price_syp=0, access_mode='membership_credit', member_only=True,
            guest_allowed=False, max_concurrent_devices=2)
        entitlement = create_entitlement(package, member=member, subscription=subscription)
        barrier = Barrier(2)
        errors = []
        session_ids = []

        def start():
            close_old_connections()
            try:
                barrier.wait()
                session_ids.append(start_usage_session(
                    InternetEntitlement.objects.get(pk=entitlement.pk)).pk)
            except Exception as exc:  # Captured so failures are asserted in the main test thread.
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [Thread(target=start) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()

        self.assertEqual(len(session_ids), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(sum(InternetSession.objects.filter(status='active').values_list(
            'reserved_minutes', flat=True)), 60)


class PartnerSnapshotTests(TestCase):
    def package(self, **overrides):
        values = dict(name_ar='باقة شريك', duration_minutes=60, price_syp=1000,
            access_mode=InternetPackage.AccessMode.TIMED_SESSION, session_minutes_limit=60)
        values.update(overrides)
        return InternetPackage.objects.create(**values)

    def test_default_partner_and_percentage_are_inherited(self):
        partner = InternetPartner.objects.create(
            name='Default ISP', is_default=True, revenue_share_percent=Decimal('30'))

        entitlement = create_entitlement(self.package())

        self.assertEqual(entitlement.partner, partner)
        self.assertEqual(entitlement.revenue_share.share_percent, Decimal('30'))
        self.assertEqual(entitlement.revenue_share.partner_amount_syp, Decimal('300'))

    def test_explicit_active_package_partner_overrides_default(self):
        InternetPartner.objects.create(
            name='Default ISP', is_default=True, revenue_share_percent=Decimal('30'))
        override = InternetPartner.objects.create(
            name='Special ISP', revenue_share_percent=Decimal('25'))

        entitlement = create_entitlement(self.package(partner=override))

        self.assertEqual(entitlement.partner, override)
        self.assertEqual(entitlement.revenue_share.share_percent, Decimal('25'))

    def test_package_percentage_overrides_effective_partner_percentage(self):
        partner = InternetPartner.objects.create(
            name='Default ISP', is_default=True, revenue_share_percent=Decimal('30'))

        entitlement = create_entitlement(
            self.package(partner_share_percent=Decimal('20')))

        self.assertEqual(entitlement.partner, partner)
        self.assertEqual(entitlement.revenue_share.share_percent, Decimal('20'))

    def test_sale_without_default_or_package_partner_has_no_share(self):
        entitlement = create_entitlement(self.package())

        self.assertIsNone(entitlement.partner)
        self.assertFalse(InternetRevenueShare.objects.filter(entitlement=entitlement).exists())

    def test_inactive_partner_cannot_be_default_and_is_never_implicitly_selected(self):
        inactive = InternetPartner(name='Inactive ISP', active=False, is_default=True)
        with self.assertRaises(ValidationError):
            inactive.save()
        inactive.is_default = False
        inactive.save()
        fallback = InternetPartner.objects.create(name='Default ISP', is_default=True)

        entitlement = create_entitlement(self.package(partner=inactive))

        self.assertEqual(get_default_internet_partner(), fallback)
        self.assertEqual(entitlement.partner, fallback)

    def test_only_one_default_partner_is_allowed(self):
        InternetPartner.objects.create(name='First', is_default=True)

        with self.assertRaises(ValidationError):
            InternetPartner.objects.create(name='Second', is_default=True)

    def test_default_changes_do_not_rewrite_snapshots_or_dashboard_history(self):
        old_partner = InternetPartner.objects.create(
            name='Old default', is_default=True, revenue_share_percent=Decimal('30'))
        package = self.package()
        old_entitlement = create_entitlement(package, idempotency_key='historical-sale')
        old_share = old_entitlement.revenue_share
        old_partner.is_default = False
        old_partner.revenue_share_percent = Decimal('90')
        old_partner.save()
        new_partner = InternetPartner.objects.create(
            name='New default', is_default=True, revenue_share_percent=Decimal('20'))

        new_entitlement = create_entitlement(package, idempotency_key='new-sale')
        retry = create_entitlement(package, idempotency_key='historical-sale')
        old_entitlement.refresh_from_db()
        old_share.refresh_from_db()

        self.assertEqual(retry.pk, old_entitlement.pk)
        self.assertEqual(old_entitlement.partner, old_partner)
        self.assertEqual((old_share.partner, old_share.share_percent),
                         (old_partner, Decimal('30')))
        self.assertEqual((new_entitlement.partner, new_entitlement.revenue_share.share_percent),
                         (new_partner, Decimal('20')))
        self.assertEqual(
            InternetRevenueShare.objects.filter(partner=old_partner).values_list(
                'entitlement_id', 'share_percent').get(),
            (old_entitlement.pk, Decimal('30')),
        )

    def test_default_override_and_history_are_immutable(self):
        partner = InternetPartner.objects.create(name='ISP', revenue_share_percent=Decimal('30'))
        package = InternetPackage.objects.create(name_ar='أسبوعي', code='partner-week', duration_minutes=0,
            price_syp=1000, access_mode='unlimited', validity_value=1, validity_unit='weeks',
            partner=partner, partner_share_percent=Decimal('25'))
        ent = create_entitlement(package)
        share = InternetRevenueShare.objects.get(entitlement=ent)
        self.assertEqual(share.partner_amount_syp, Decimal('250.00'))
        partner.revenue_share_percent = 50; partner.save()
        create_entitlement(package, idempotency_key='another')
        share.refresh_from_db(); self.assertEqual(share.share_percent, Decimal('25'))
        self.assertEqual(InternetRevenueShare.objects.filter(entitlement=ent).count(), 1)


class HardeningRegressionTests(TestCase):
    def setUp(self):
        self.package = InternetPackage.objects.create(name_ar='ساعتان', code='hardening-2h', duration_minutes=120,
            price_syp=1000, access_mode='timed_session', session_minutes_limit=120)

    def test_runtime_expiry_blocks_operations_and_metrics_without_cleanup(self):
        ent = create_entitlement(self.package)
        InternetEntitlement.objects.filter(pk=ent.pk).update(valid_until=timezone.now() - timedelta(seconds=1))
        ent.refresh_from_db()
        self.assertEqual(ent.status, 'active')
        self.assertEqual(ent.effective_status(), 'expired')
        self.assertFalse(effectively_active_entitlements().filter(pk=ent.pk).exists())
        with self.assertRaises(ValidationError): start_usage_session(ent)
        with self.assertRaises(ValidationError): register_device(ent, 'AA:BB')
        with self.assertRaises(ValidationError): ManualNetworkBackend().provision_access(ent)
        self.assertEqual(InternetAccessDevice.objects.count(), 0)

    def test_allowance_overrun_preserves_actual_and_caps_consumption(self):
        package = InternetPackage.objects.create(name_ar='17 دقيقة', code='17m', duration_minutes=0,
            price_syp=1, access_mode='allowance', total_minutes_limit=17)
        ent = create_entitlement(package)
        session = start_usage_session(ent)
        end_usage_session(session, at=session.started_at + timedelta(minutes=40))
        session.refresh_from_db(); ent.refresh_from_db()
        self.assertEqual(session.actual_duration_minutes, 40)
        self.assertEqual(session.allowance_minutes_consumed, 17)
        self.assertEqual(ent.minutes_remaining, 0)
        end_usage_session(session, at=session.started_at + timedelta(minutes=50))
        ent.refresh_from_db(); self.assertEqual(ent.minutes_used, 17)

    def test_on_first_use_is_only_usage_session_start(self):
        package = InternetPackage.objects.create(name_ar='أسبوع', code='first-use-only', duration_minutes=0,
            price_syp=1, access_mode='unlimited', activation_policy='on_first_use', validity_value=1, validity_unit='weeks')
        ent = create_entitlement(package)
        ManualNetworkBackend().provision_access(ent); ManualNetworkBackend().refresh_access(ent)
        ent.refresh_from_db()
        self.assertIsNone(ent.activated_at); self.assertEqual(ent.status, 'pending')
        start_usage_session(ent); ent.refresh_from_db()
        self.assertIsNotNone(ent.activated_at); self.assertEqual(ent.status, 'active')

    def test_package_edits_do_not_change_sold_terms(self):
        partner = InternetPartner.objects.create(name='A', revenue_share_percent=Decimal('20'))
        profile = InternetBandwidthProfile.objects.create(code='old-speed', name='Old')
        self.package.partner = partner; self.package.partner_share_percent = Decimal('25')
        self.package.bandwidth_profile = profile; self.package.validity_value = 30; self.package.validity_unit = 'days'
        self.package.max_concurrent_devices = 2; self.package.max_registered_devices = 3
        self.package.total_minutes_limit = 200; self.package.save()
        ent = create_entitlement(self.package); share = ent.revenue_share
        other = InternetPartner.objects.create(name='B', revenue_share_percent=Decimal('90'))
        self.package.price_syp = 9999; self.package.validity_value = 1; self.package.bandwidth_profile = None
        self.package.max_concurrent_devices = self.package.max_registered_devices = 1
        self.package.total_minutes_limit = 2; self.package.partner = other; self.package.partner_share_percent = 90; self.package.save()
        ent.refresh_from_db(); share.refresh_from_db()
        self.assertEqual((ent.gross_amount_syp, ent.validity_value, ent.bandwidth_profile_code,
            ent.max_concurrent_devices, ent.max_registered_devices, ent.total_minutes_allowed, ent.partner_id,
            share.share_percent), (Decimal('1000'), 30, 'old-speed', 2, 3, 200, partner.pk, Decimal('25')))

    def test_revenue_adjustment_is_immutable_and_idempotent(self):
        partner = InternetPartner.objects.create(name='ISP', revenue_share_percent=Decimal('25'))
        self.package.partner = partner; self.package.save()
        order = Order.objects.create(); payment = Payment.objects.create(order=order, amount_syp=1000, method='cash')
        ent = create_entitlement(self.package, order=order, payment=payment)
        first = record_payment_reversal_adjustment(ent.revenue_share, payment=payment)
        second = record_payment_reversal_adjustment(ent.revenue_share, payment=payment)
        self.assertEqual(first.pk, second.pk); self.assertEqual(InternetRevenueShareAdjustment.objects.count(), 1)
        self.assertEqual(first.partner_delta_syp, Decimal('-250'))
        ent.revenue_share.refresh_from_db(); self.assertEqual(ent.revenue_share.share_percent, Decimal('25'))


class InternetHttpWorkflowTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        self.staff = get_user_model().objects.create_user(username='cash', phone='0900', password='x', role='cashier')
        self.client = Client(); self.client.force_login(self.staff)
        self.partner = InternetPartner.objects.create(name='ISP', revenue_share_percent=Decimal('30'))
        self.cashbox = FinancialAccount.objects.create(
            code='cash:internet', name_ar='صندوق الإنترنت', account_type='asset',
            scope='cashbox', is_active=True, negative_balance_policy='allow')
        FinancialAccount.objects.create(
            code='revenue:internet', name_ar='إيراد الإنترنت', account_type='revenue',
            scope='operating', is_active=True, negative_balance_policy='allow')
        self.package = InternetPackage.objects.create(name_ar='أسبوعي', code='http-week', duration_minutes=0,
            price_syp=2000, access_mode='unlimited', validity_value=1, validity_unit='weeks', partner=self.partner)

    def test_duplicate_sale_post_creates_one_commercial_identity(self):
        data = {'customer_kind': 'guest', 'guest_name': 'Guest', 'package': self.package.pk,
                'payment_method': 'cash', 'idempotency_key': 'browser-post-1'}
        self.client.post(reverse('staff_internet_sale'), data)
        self.client.post(reverse('staff_internet_sale'), data)
        ent = InternetEntitlement.objects.get()
        self.assertEqual(Order.objects.count(), 1); self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(InternetRevenueShare.objects.count(), 1)
        self.assertEqual(ent.network_status, 'provisioned'); self.assertTrue(ent.external_network_identifier == '')

    def test_cash_sale_posts_once_to_cashbox_and_daily_close(self):
        first = create_commercial_sale(
            self.package, payment_method=Payment.Method.CASH, actor=self.staff,
            idempotency_key='cash-finance-once')
        again = create_commercial_sale(
            self.package, payment_method=Payment.Method.CASH, actor=self.staff,
            idempotency_key='cash-finance-once')

        self.assertEqual(first.pk, again.pk)
        self.assertEqual(Payment.objects.filter(order=first.order).count(), 1)
        movement = CashMovement.objects.get(related_order=first.order)
        self.assertEqual((movement.financial_account, movement.amount_syp),
                         (self.cashbox, Decimal('2000')))
        self.assertEqual(PostingCommand.objects.filter(
            key='internet-sale:cash-finance-once:payment').count(), 1)
        self.assertEqual(PostingBatch.objects.filter(
            operation_type='order_payment.collect').count(), 1)
        totals = close_totals(self.cashbox, timezone.localdate())
        self.assertEqual(totals['cash_receipts'], Decimal('2000'))

    def test_cash_sale_respects_closed_period_without_partial_commercial_records(self):
        DailyClose.objects.create(
            account=self.cashbox, business_date=timezone.localdate(),
            status=DailyClose.Status.CLOSED, is_finalized=True)

        with self.assertRaises(ClosedPeriodError):
            create_commercial_sale(
                self.package, payment_method=Payment.Method.CASH, actor=self.staff,
                idempotency_key='cash-closed-period')

        self.assertFalse(InternetEntitlement.objects.filter(
            idempotency_key='cash-closed-period').exists())
        self.assertFalse(Order.objects.exists())

    def test_router_failure_is_persisted_and_retried_without_duplicate_sale(self):
        backend = ManualNetworkBackend()
        with patch('core.services.network_backends.get_network_backend', return_value=backend), \
             patch.object(backend, 'provision_access', side_effect=RuntimeError('router offline')):
            failed = create_commercial_sale(
                self.package, payment_method=Payment.Method.CASH, actor=self.staff,
                idempotency_key='router-retry')
        self.assertEqual(failed.network_status, InternetEntitlement.NetworkStatus.PROVISION_ERROR)
        self.assertIn('router offline', failed.last_network_error)

        retried = create_commercial_sale(
            self.package, payment_method=Payment.Method.CASH, actor=self.staff,
            idempotency_key='router-retry')
        self.assertEqual(retried.network_status, InternetEntitlement.NetworkStatus.PROVISIONED)
        self.assertEqual((Order.objects.count(), Payment.objects.count()), (1, 1))

    def test_expired_is_rendered_and_not_in_staff_metric(self):
        ent = create_entitlement(self.package)
        InternetEntitlement.objects.filter(pk=ent.pk).update(valid_until=timezone.now() - timedelta(minutes=1))
        response = self.client.get(reverse('staff_internet'))
        self.assertContains(response, 'منتهي'); self.assertEqual(response.context['internet_metrics']['active_passes'], 0)

    def unpaid_metric(self):
        return self.client.get(reverse('staff_internet')).context['internet_metrics']['unpaid']

    def test_active_unpaid_commercial_entitlement_counts_once(self):
        unpaid = create_commercial_sale(
            self.package, payment_method=Payment.Method.UNPAID,
            actor=self.staff, idempotency_key='metric-unpaid')

        self.assertTrue(unpaid.is_effectively_active)
        self.assertEqual(self.unpaid_metric(), 1)

    def test_paid_entitlement_does_not_count_as_unpaid(self):
        create_commercial_sale(
            self.package, payment_method=Payment.Method.CASH,
            actor=self.staff, idempotency_key='metric-paid')

        self.assertEqual(self.unpaid_metric(), 0)

    def test_complimentary_entitlement_does_not_count_as_unpaid(self):
        complimentary = InternetPackage.objects.create(
            name_ar='مجاني', code='metric-free', duration_minutes=30,
            price_syp=0, access_mode=InternetPackage.AccessMode.TIMED_SESSION,
            session_minutes_limit=30)
        create_commercial_sale(
            complimentary, payment_method=Payment.Method.FREE,
            actor=self.staff, idempotency_key='metric-free')

        self.assertEqual(self.unpaid_metric(), 0)

    def test_membership_credit_entitlement_does_not_count_as_unpaid(self):
        member = Member.objects.create(name_ar='عضو', phone='0999000000')
        plan = MembershipPlan.objects.create(code='metric-plan', name_ar='عضوية')
        subscription = MembershipSubscription.objects.create(
            member=member, plan=plan, starts_at=timezone.now(),
            remaining_internet_minutes=60)
        credit = InternetPackage.objects.create(
            name_ar='رصيد عضوية', code='metric-credit', duration_minutes=0,
            price_syp=0, access_mode=InternetPackage.AccessMode.MEMBERSHIP_CREDIT,
            member_only=True, guest_allowed=False)
        create_commercial_sale(
            credit, payment_method=Payment.Method.FREE, member=member,
            subscription=subscription, actor=self.staff, idempotency_key='metric-credit')

        self.assertEqual(self.unpaid_metric(), 0)

    def test_staff_page_labels_manual_backend_without_claiming_router_provisioning(self):
        create_commercial_sale(
            self.package, payment_method=Payment.Method.CASH,
            actor=self.staff, idempotency_key='manual-label')

        response = self.client.get(reverse('staff_internet'))

        self.assertContains(response, 'يدوي (بدون راوتر)')
        self.assertContains(response, 'تجهيز يدوي — أو بانتظار الربط بالشبكة')
        self.assertNotContains(response, 'مجهز على الشبكة')

    def test_commercial_sale_is_primary_and_legacy_session_form_remains(self):
        response = self.client.get(reverse('staff_internet'))
        content = response.content.decode()

        self.assertContains(response, 'action="{}"'.format(reverse('staff_internet_sale')))
        self.assertContains(response, 'action="{}"'.format(reverse('staff_internet_start')))
        self.assertContains(response, 'جلسة يدوية بدون باقة (خيار متقدم)')
        self.assertLess(content.index(reverse('staff_internet_sale')),
                        content.index(reverse('staff_internet_start')))

    def test_partner_cross_scope_and_expired_rendering(self):
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.create_user(username='partner', phone='0901', password='x')
        InternetPartnerUser.objects.create(partner=self.partner, user=user)
        own = create_entitlement(self.package)
        InternetEntitlement.objects.filter(pk=own.pk).update(valid_until=timezone.now() - timedelta(minutes=1))
        other_partner = InternetPartner.objects.create(name='Other')
        other_package = InternetPackage.objects.create(name_ar='سري', code='secret', duration_minutes=2,
            price_syp=99, access_mode='timed_session', partner=other_partner)
        other = create_entitlement(other_package, guest_name='Hidden customer')
        self.client.force_login(user); response = self.client.get(reverse('internet_partner_dashboard'))
        self.assertContains(response, own.access_code); self.assertContains(response, 'expired')
        self.assertNotContains(response, other.access_code); self.assertNotContains(response, 'Hidden customer')

    def test_partner_net_revenue_ignores_unpaid_and_nets_reversal(self):
        unpaid_data = {'customer_kind': 'guest', 'package': self.package.pk,
            'payment_method': 'unpaid', 'idempotency_key': 'unpaid'}
        self.client.post(reverse('staff_internet_sale'), unpaid_data)
        paid_data = {**unpaid_data, 'payment_method': 'cash', 'idempotency_key': 'paid'}
        self.client.post(reverse('staff_internet_sale'), paid_data)
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.create_user(username='revenue-partner', phone='0902', password='x')
        InternetPartnerUser.objects.create(partner=self.partner, user=user)
        self.client.force_login(user)
        response = self.client.get(reverse('internet_partner_dashboard'))
        self.assertEqual(response.context['totals']['gross'], Decimal('2000'))
        paid_share = InternetRevenueShare.objects.get(entitlement__idempotency_key='paid')
        Payment.objects.filter(pk=paid_share.payment_id).update(is_reversed=True, is_active=False,
            reversed_at=timezone.now(), reversal_reason='other')
        response = self.client.get(reverse('internet_partner_dashboard'))
        self.assertEqual(response.context['totals']['gross'], Decimal('0'))
        record_payment_reversal_adjustment(paid_share)
        response = self.client.get(reverse('internet_partner_dashboard'))
        self.assertEqual(response.context['totals']['gross'], Decimal('0'))
        self.assertEqual(response.context['totals']['partner'], Decimal('0'))


class PartnerDashboardDateRangeTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        self.partner = InternetPartner.objects.create(
            name='Date range ISP', revenue_share_percent=Decimal('30'))
        FinancialAccount.objects.create(
            code='cash:date-range', name_ar='صندوق', account_type='asset', scope='cashbox',
            is_active=True, negative_balance_policy='allow')
        FinancialAccount.objects.create(
            code='revenue:date-range', name_ar='إيراد', account_type='revenue', scope='operating',
            is_active=True, negative_balance_policy='allow')
        self.package = InternetPackage.objects.create(
            name_ar='نطاق زمني', code='date-range', duration_minutes=60,
            price_syp=2000, access_mode='timed_session',
            session_minutes_limit=60, partner=self.partner)
        self.user = get_user_model().objects.create_user(
            username='date-partner', phone='0903', password='x')
        InternetPartnerUser.objects.create(partner=self.partner, user=self.user)
        self.client.force_login(self.user)
        self.url = reverse('internet_partner_dashboard')

    def test_malformed_date_returns_controlled_validation_response(self):
        response = self.client.get(self.url, {'start': 'not-a-date', 'end': '2026-08-11'})

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'role="alert"', status_code=400)

    def test_impossible_calendar_date_returns_controlled_validation_response(self):
        response = self.client.get(self.url, {'start': '2026-02-30', 'end': '2026-03-01'})

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'role="alert"', status_code=400)

    def test_inverted_range_returns_controlled_validation_response(self):
        response = self.client.get(self.url, {'start': '2026-08-12', 'end': '2026-08-11'})

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'تاريخ البداية', status_code=400)

    def test_reporting_interval_is_bounded(self):
        response = self.client.get(self.url, {'start': '2025-01-01', 'end': '2026-08-11'})

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, '366', status_code=400)

    def test_boundary_dates_are_included_and_outside_records_are_excluded(self):
        shares = []
        for key in ('range-start', 'range-end', 'range-outside'):
            entitlement = create_commercial_sale(
                self.package, payment_method=Payment.Method.CASH,
                actor=self.user, idempotency_key=key)
            shares.append(entitlement.revenue_share)
        InternetRevenueShare.objects.filter(pk=shares[0].pk).update(
            business_date=date(2026, 8, 1))
        InternetRevenueShare.objects.filter(pk=shares[1].pk).update(
            business_date=date(2026, 8, 11))
        InternetRevenueShare.objects.filter(pk=shares[2].pk).update(
            business_date=date(2026, 8, 12))
        InternetRevenueShareAdjustment.objects.create(
            revenue_share=shares[0], kind='correction', idempotency_key='boundary-adjustment',
            gross_delta_syp=Decimal('-100'), partner_delta_syp=Decimal('-30'),
            hub_delta_syp=Decimal('-70'), business_date=date(2026, 8, 11))
        InternetRevenueShareAdjustment.objects.create(
            revenue_share=shares[0], kind='correction', idempotency_key='outside-adjustment',
            gross_delta_syp=Decimal('-500'), partner_delta_syp=Decimal('-150'),
            hub_delta_syp=Decimal('-350'), business_date=date(2026, 7, 31))

        response = self.client.get(self.url, {'start': '2026-08-01', 'end': '2026-08-11'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.context['shares'].values_list('pk', flat=True)),
            {shares[0].pk, shares[1].pk})
        self.assertEqual(response.context['totals']['gross'], Decimal('3900'))
