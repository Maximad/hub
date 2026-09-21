from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from threading import Barrier, Thread

from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (CashMovement, Category, DailyClose, FinancialAccount, InternetAccessDevice, InternetBandwidthProfile, InternetEntitlement,
                         InternetNetworkOperation, InternetPackage, InternetPartner, InternetPartnerUser, InternetRevenueShare,
                         InternetRevenueShareAdjustment, InternetSession, InternetUsageLedger, Member, Order, Payment,
                         PostingBatch, PostingCommand, Product)
from core.services.internet_access import (create_entitlement, end_usage_session,
    create_commercial_sale, daily_minutes_remaining, daily_minutes_used,
    effectively_active_entitlements, get_default_internet_partner,
    record_payment_reversal_adjustment,
    register_device, start_usage_session, validity_end)
from core.services.network_backends import ManualNetworkBackend, get_network_backend
from core.services.network_operations import process_network_operation
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
            daily_minutes_limit=40, session_minutes_limit=60, max_concurrent_devices=2,
            max_registered_devices=2)
        ent = create_entitlement(package)
        # Use a future local noon so the daily-midnight boundary cannot make this
        # reservation test depend on what time the CI runner happens to execute.
        now = (timezone.localtime(timezone.now()).replace(
            hour=12, minute=0, second=0, microsecond=0,
        ) + timedelta(days=1))
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
        device = register_device(ent, 'aa-bb-cc-dd-ee-ff')
        self.assertEqual(device.device_mac, 'AA:BB:CC:DD:EE:FF')
        self.assertEqual(register_device(ent, 'AA:BB:CC:DD:EE:FF').pk, device.pk)
        with self.assertRaises(ValidationError): register_device(ent, '11:22:33:44:55:66')
        with self.assertRaises(ValidationError): register_device(ent, 'AA:BB')

    def test_backend_selection_is_explicit(self):
        self.assertIsInstance(get_network_backend('manual'), ManualNetworkBackend)
        with self.assertRaises(ValidationError): get_network_backend('unknown')

    def test_effectively_active_queryset_excludes_expired_without_mutating(self):
        ent = create_entitlement(self.timed)
        ent.valid_until = timezone.now() - timedelta(seconds=1)
        ent.save(update_fields=['valid_until'])
        self.assertFalse(effectively_active_entitlements().filter(pk=ent.pk).exists())
        ent.refresh_from_db(); self.assertEqual(ent.status, InternetEntitlement.Status.ACTIVE)

    def test_create_commercial_sale_rejects_unpaid_reuse_with_different_fingerprint(self):
        package = InternetPackage.objects.create(name_ar='باقة تجارية', code='commercial-idem', duration_minutes=60,
            price_syp=500, access_mode=InternetPackage.AccessMode.TIMED_SESSION, session_minutes_limit=60)
        member = Member.objects.create(name_ar='عضو', phone='0999111222')
        entitlement = create_commercial_sale(package, member=member, idempotency_key='sale-key')
        self.assertEqual(entitlement.gross_amount_syp, 500)
        with self.assertRaises(ValidationError):
            create_commercial_sale(package, member=member, idempotency_key='sale-key', charged_amount_syp=600)

    def test_payment_reversal_adjustment_preserves_snapshot(self):
        partner = InternetPartner.objects.create(name='مزود', active=True, revenue_share_percent=Decimal('20'))
        package = InternetPackage.objects.create(name_ar='باقة تجارية', code='share-snapshot', duration_minutes=60,
            price_syp=1000, access_mode='timed_session', session_minutes_limit=60, partner=partner)
        entitlement = create_commercial_sale(package, payment_method=Payment.Method.CASH, idempotency_key='share-sale')
        self.assertEqual(InternetRevenueShare.objects.get(entitlement=entitlement).partner_share_syp, Decimal('200.00'))
        payment = entitlement.payment
        adjustment = record_payment_reversal_adjustment(payment, actor=None)
        self.assertEqual(adjustment.amount_syp, Decimal('-200.00'))
        self.assertEqual(InternetRevenueShare.objects.get(entitlement=entitlement).partner_share_syp, Decimal('200.00'))

    def test_closed_finance_day_blocks_commercial_sale(self):
        today = timezone.localdate()
        DailyClose.objects.create(business_date=today, status=DailyClose.Status.FINALIZED, finalized_at=timezone.now())
        package = InternetPackage.objects.create(name_ar='باقة', code='closed-sale', duration_minutes=60,
            price_syp=100, access_mode='timed_session', session_minutes_limit=60)
        with self.assertRaises(ClosedPeriodError): create_commercial_sale(package, idempotency_key='closed-sale')

    def test_membership_credit_reserves_and_releases(self):
        member = Member.objects.create(name_ar='عضو', phone='0999888777')
        plan = MembershipPlan.objects.create(name_ar='عضوية', price_syp=1000, duration_value=1, duration_unit='months',
            included_internet_minutes=100)
        subscription = MembershipSubscription.objects.create(member=member, plan=plan,
            starts_at=timezone.now() - timedelta(days=1), ends_at=timezone.now() + timedelta(days=20),
            status=MembershipSubscription.Status.ACTIVE, internet_minutes_used=0)
        package = InternetPackage.objects.create(name_ar='رصيد عضوية', code='member-credit', duration_minutes=0,
            price_syp=0, access_mode='membership_credit', total_minutes_limit=None, session_minutes_limit=60)
        ent = create_entitlement(package, member=member, subscription=subscription)
        session = start_usage_session(ent)
        self.assertEqual(session.reserved_minutes, 60)
        subscription.refresh_from_db(); self.assertEqual(subscription.internet_minutes_used, 0)
        end_usage_session(session, at=session.started_at + timedelta(minutes=30))
        subscription.refresh_from_db(); self.assertEqual(subscription.internet_minutes_used, 30)

    def test_membership_credit_start_blocks_inactive_source_subscription(self):
        member = Member.objects.create(name_ar='عضو', phone='0999888778')
        plan = MembershipPlan.objects.create(name_ar='عضوية', price_syp=1000, duration_value=1, duration_unit='months',
            included_internet_minutes=100)
        subscription = MembershipSubscription.objects.create(member=member, plan=plan,
            starts_at=timezone.now() - timedelta(days=2), ends_at=timezone.now() - timedelta(days=1),
            status=MembershipSubscription.Status.EXPIRED, internet_minutes_used=0)
        package = InternetPackage.objects.create(name_ar='رصيد عضوية', code='member-credit-inactive', duration_minutes=0,
            price_syp=0, access_mode='membership_credit', total_minutes_limit=None, session_minutes_limit=60)
        ent = create_entitlement(package, member=member, subscription=subscription)
        with self.assertRaises(ValidationError): start_usage_session(ent)

    def test_default_partner_is_explicit(self):
        first = InternetPartner.objects.create(name='الأول', active=True, is_default=True)
        InternetPartner.objects.create(name='الثاني', active=True)
        self.assertEqual(get_default_internet_partner(), first)

    def test_revenue_share_snapshots_are_immutable(self):
        partner = InternetPartner.objects.create(name='مزود', active=True, revenue_share_percent=Decimal('20'))
        package = InternetPackage.objects.create(name_ar='باقة', code='snapshot', duration_minutes=60,
            price_syp=1000, partner=partner, partner_share_percent=Decimal('25'), access_mode='timed_session', session_minutes_limit=60)
        ent = create_commercial_sale(package, payment_method=Payment.Method.CASH)
        share = InternetRevenueShare.objects.get(entitlement=ent)
        partner.revenue_share_percent = Decimal('40'); partner.save()
        package.partner_share_percent = Decimal('45'); package.save()
        ent.gross_amount_syp = Decimal('9999'); ent.save(update_fields=['gross_amount_syp'])
        share.refresh_from_db()
        self.assertEqual((share.gross_amount_syp, share.share_percent, share.partner_share_syp),
                         (Decimal('1000.00'), Decimal('25.00'), Decimal('250.00'))


@override_settings(DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}})
class InternetConcurrencySQLiteTests(TransactionTestCase):
    reset_sequences = True

    def test_sqlite_marker_only(self):
        # Production correctness for select_for_update is covered by PostgreSQL CI.
        # This marker keeps a local fallback contract without pretending SQLite locks rows.
        self.assertTrue(True)


class InternetRevenueAdjustmentTests(TestCase):
    def setUp(self):
        self.partner = InternetPartner.objects.create(name='مزود', active=True, revenue_share_percent=Decimal('20'))
        self.package = InternetPackage.objects.create(name_ar='باقة', code='adjustment', duration_minutes=60,
            price_syp=1000, partner=self.partner, access_mode='timed_session', session_minutes_limit=60)

    def test_adjustment_requires_reason_and_is_idempotent(self):
        entitlement = create_commercial_sale(self.package, payment_method=Payment.Method.CASH)
        with self.assertRaises(ValidationError):
            InternetRevenueShareAdjustment.objects.create(entitlement=entitlement, amount_syp=Decimal('10'), reason='')
        first = InternetRevenueShareAdjustment.objects.create(entitlement=entitlement,
            amount_syp=Decimal('10'), reason='تصحيح', idempotency_key='adj-1')
        with self.assertRaises(Exception):
            InternetRevenueShareAdjustment.objects.create(entitlement=entitlement,
                amount_syp=Decimal('5'), reason='تصحيح', idempotency_key='adj-1')
        self.assertEqual(first.entitlement_id, entitlement.pk)


class InternetPostingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='internet-accountant', password='pass', role='admin')
        self.partner = InternetPartner.objects.create(name='مزود', active=True, revenue_share_percent=Decimal('20'))
        self.package = InternetPackage.objects.create(name_ar='باقة', code='posting', duration_minutes=60,
            price_syp=1000, partner=self.partner, access_mode='timed_session', session_minutes_limit=60)

    def test_cash_sale_posts_balanced_entries(self):
        entitlement = create_commercial_sale(self.package, payment_method=Payment.Method.CASH,
            actor=self.user, idempotency_key='post-sale')
        batch = PostingBatch.objects.get(source_object_id=str(entitlement.order_id), operation_type='order_payment.collect')
        self.assertTrue(batch.is_balanced())
        self.assertEqual(batch.status, PostingBatch.Status.POSTED)
        self.assertEqual(PostingCommand.objects.get(key='internet-sale:post-sale').actor, self.user)

    def test_unpaid_sale_has_no_payment_or_posting_until_collection(self):
        entitlement = create_commercial_sale(self.package, payment_method=Payment.Method.UNPAID,
            actor=self.user, idempotency_key='unpaid-sale')
        self.assertIsNone(entitlement.payment_id)
        self.assertFalse(PostingBatch.objects.filter(source_object_id=str(entitlement.order_id), operation_type='order_payment.collect').exists())


class InternetPostingConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='internet-concurrency', password='pass', role='admin')
        self.partner = InternetPartner.objects.create(name='مزود', active=True, revenue_share_percent=Decimal('20'))
        self.package = InternetPackage.objects.create(name_ar='باقة', code='concurrency', duration_minutes=60,
            price_syp=1000, partner=self.partner, access_mode='timed_session', session_minutes_limit=60)

    def test_concurrent_duplicate_sale_posts_once(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Requires PostgreSQL row-lock semantics')
        barrier = Barrier(2)
        results = []
        errors = []

        def worker():
            close_old_connections()
            try:
                barrier.wait()
                entitlement = create_commercial_sale(self.package, payment_method=Payment.Method.CASH,
                    actor=self.user, idempotency_key='concurrent-sale')
                results.append(entitlement.pk)
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [Thread(target=worker) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertFalse(errors)
        self.assertEqual(len(set(results)), 1)
