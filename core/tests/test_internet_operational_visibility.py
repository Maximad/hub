from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    ActivityLog,
    InternetEntitlement,
    InternetPackage,
    InternetPartner,
    InternetPartnerUser,
    InternetSession,
    Member,
)
from core.services.internet_access import create_entitlement, start_usage_session
from core.templatetags.internet_visibility import provider_network_visibility
from internet.models import InternetOperationsState, InternetSessionNetworkOperation


@override_settings(SECURE_SSL_REDIRECT=False, MIKROTIK_ENABLED=False)
class InternetOperationalVisibilityTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.partner = InternetPartner.objects.create(
            name='Default ISP', active=True, is_default=True, revenue_share_percent=30,
        )
        self.other_partner = InternetPartner.objects.create(
            name='Other ISP', active=True, revenue_share_percent=20,
        )
        self.provider = User.objects.create_user(
            username='visibility-provider', phone='090-visibility-provider', password='pass',
            role=User.Role.INTERNET_PROVIDER,
        )
        InternetPartnerUser.objects.create(partner=self.partner, user=self.provider)
        self.member = Member.objects.create(name_ar='عميل ظاهر', phone='0910000001')
        self.other_member = Member.objects.create(name_ar='عميل مخفي', phone='0910000002')
        self.package = InternetPackage.objects.create(
            name_ar='باقة ظاهرة', code='visibility-own', price_syp=100,
            access_mode=InternetPackage.AccessMode.ALLOWANCE,
            total_minutes_limit=300, partner=self.partner,
        )
        self.other_package = InternetPackage.objects.create(
            name_ar='باقة مخفية', code='visibility-other', price_syp=100,
            access_mode=InternetPackage.AccessMode.ALLOWANCE,
            total_minutes_limit=300, partner=self.other_partner,
        )
        self.entitlement = create_entitlement(self.package, member=self.member)
        self.entitlement.network_status = InternetEntitlement.NetworkStatus.PROVISIONED
        self.entitlement.save(update_fields=['network_status'])
        self.other_entitlement = create_entitlement(self.other_package, member=self.other_member)
        self.other_entitlement.network_status = InternetEntitlement.NetworkStatus.PROVISIONED
        self.other_entitlement.save(update_fields=['network_status'])
        self.entitlement_session = start_usage_session(self.entitlement)
        self.other_session = start_usage_session(self.other_entitlement)
        self.direct_ready = InternetSession.objects.create(
            member=self.member,
            start_time=timezone.now(),
            billing_mode=InternetSession.BillingMode.OPEN_METERED,
            status=InternetSession.Status.ACTIVE,
            network_provider=InternetSession.NetworkProvider.MIKROTIK,
            network_status='provisioned',
        )
        self.direct_pending = InternetSession.objects.create(
            member=self.member,
            start_time=timezone.now(),
            billing_mode=InternetSession.BillingMode.OPEN_METERED,
            status=InternetSession.Status.ACTIVE,
            network_provider=InternetSession.NetworkProvider.MIKROTIK,
            network_status='not_provisioned',
        )
        self.unassigned_manual = InternetSession.objects.create(
            member=self.member,
            start_time=timezone.now(),
            billing_mode=InternetSession.BillingMode.OPEN_METERED,
            status=InternetSession.Status.ACTIVE,
            network_provider=InternetSession.NetworkProvider.MANUAL,
            network_status='provisioned',
        )
        InternetSessionNetworkOperation.objects.create(
            session=self.direct_ready,
            operation=InternetSessionNetworkOperation.Operation.PROVISION,
            status=InternetSessionNetworkOperation.Status.FAILED,
            idempotency_key='visibility-direct-failure',
            attempt_count=2,
            last_error='authentication credential=secret-value failed',
        )
        InternetSessionNetworkOperation.objects.create(
            session=self.other_session,
            operation=InternetSessionNetworkOperation.Operation.PROVISION,
            status=InternetSessionNetworkOperation.Status.FAILED,
            idempotency_key='visibility-hidden-failure',
            last_error='authentication credential=hidden-secret failed',
        )
        self.state = InternetOperationsState.objects.create(
            key='default',
            last_worker_seen_at=timezone.now(),
            last_mikrotik_check_at=timezone.now(),
            last_mikrotik_check_ok=True,
        )
        ActivityLog.objects.create(
            action='internet.mikrotik_readonly_healthcheck', details={'ok': True},
        )
        self.client.force_login(self.provider)

    def test_visibility_counts_are_provider_scoped_and_not_live_device_claims(self):
        visibility = provider_network_visibility(self.partner, self.state)

        self.assertEqual(visibility['hub_active_sessions'], 3)
        self.assertEqual(visibility['network_ready_sessions'], 2)
        self.assertEqual(visibility['network_pending_sessions'], 1)
        self.assertEqual(visibility['network_failed_sessions'], 0)
        self.assertFalse(visibility['connected_devices_measured'])
        self.assertEqual(visibility['operations']['failed_total'], 1)
        self.assertEqual(len(visibility['recent_session_operations']), 1)
        self.assertEqual(
            visibility['recent_session_operations'][0]['session'].pk,
            self.direct_ready.pk,
        )

    def test_provider_network_page_includes_direct_operations_without_secrets(self):
        response = self.client.get(reverse('internet_provider_network'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'سجلات جلسات فعالة في هَبّ')
        self.assertContains(response, 'ليست عدّاداً للأجهزة المتصلة الآن')
        self.assertContains(response, 'فحص القراءة لا يثبت صلاحيات الكتابة')
        self.assertContains(response, self.direct_ready.public_code)
        self.assertContains(response, 'تعذر إكمال العملية باستخدام حساب الخدمة الحالي.')
        self.assertNotContains(response, 'secret-value')
        self.assertNotContains(response, 'hidden-secret')
        self.assertNotContains(response, self.other_session.public_code)
        self.assertNotContains(response, self.unassigned_manual.public_code)
        self.assertNotContains(response, 'جاهزية تفعيل MikroTik')

    def test_non_default_provider_does_not_inherit_package_less_mikrotik_activity(self):
        visibility = provider_network_visibility(self.other_partner, self.state)

        self.assertEqual(visibility['hub_active_sessions'], 1)
        self.assertEqual(visibility['network_ready_sessions'], 1)
        self.assertEqual(len(visibility['recent_session_operations']), 1)
        self.assertEqual(
            visibility['recent_session_operations'][0]['session'].pk,
            self.other_session.pk,
        )
        self.assertNotEqual(
            visibility['recent_session_operations'][0]['session'].pk,
            self.direct_ready.pk,
        )
