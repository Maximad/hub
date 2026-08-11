from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (InternetAccessDevice, InternetBandwidthProfile, InternetEntitlement,
                         InternetPackage, InternetPartner, InternetPartnerUser, InternetRevenueShare,
                         InternetRevenueShareAdjustment, InternetSession, Member, Order, Payment)
from core.services.internet_access import (create_entitlement, end_usage_session,
    create_commercial_sale, effectively_active_entitlements, record_payment_reversal_adjustment,
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
