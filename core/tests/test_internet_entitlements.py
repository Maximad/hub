from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.models import (InternetBandwidthProfile, InternetEntitlement,
                         InternetPackage, InternetPartner, InternetRevenueShare,
                         Member)
from core.services.internet_access import (create_entitlement, end_usage_session,
    register_device, start_usage_session, validity_end)
from core.services.network_backends import ManualNetworkBackend, get_network_backend
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


class PartnerSnapshotTests(TestCase):
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
