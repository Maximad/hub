from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    ActivityLog,
    InternetBandwidthProfile,
    InternetEntitlement,
    InternetNetworkOperation,
    InternetPartner,
    InternetRevenueShare,
    InternetSession,
    Member,
    Order,
    Payment,
)
from core.services.internet_access import start_usage_session
from core.services.internet_internal_access import (
    INTERNAL_OWNER_ORIGIN,
    INTERNAL_TEAM_ORIGIN,
    grant_internal_access,
    revoke_internal_access,
)
from core.views.internet_provider import _provider_entitlements, _provider_sessions
from internet.models import InternalStaffInternetGrant


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    MIKROTIK_ENABLED=False,
)
class InternalInternetAccessTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.actor = User.objects.create_superuser(
            username='internal-internet-admin',
            password='pass',
            email='internal-internet@example.com',
            phone='+963900009001',
        )
        self.staff_user = User.objects.create_user(
            username='salwa-staff',
            first_name='سلوى',
            last_name='السيد',
            password='pass',
            phone='+963900009002',
            role=User.Role.BARTENDER,
        )
        self.provider_user = User.objects.create_user(
            username='provider-account',
            password='pass',
            phone='+963900009003',
            role=User.Role.INTERNET_PROVIDER,
        )
        self.customer_member = Member.objects.create(
            name_ar='عضو زبون مستقل',
            phone='+963900009004',
        )
        self.profile = InternetBandwidthProfile.objects.create(
            code='fast',
            name='Fast',
            router_profile_name='hub-full',
            is_active=True,
        )

    def grant(self, **overrides):
        values = {
            'staff_user': self.staff_user,
            'grant_kind': 'team',
            'bandwidth_profile': self.profile,
            'access_mode': 'allowance',
            'validity_value': 30,
            'validity_unit': 'days',
            'session_minutes_limit': 180,
            'total_minutes_allowed': 1200,
            'daily_minutes_limit': 240,
            'max_concurrent_devices': 2,
            'max_registered_devices': 4,
            'actor': self.actor,
        }
        values.update(overrides)
        return grant_internal_access(**values)

    def test_grant_targets_staff_user_and_is_not_a_sale(self):
        entitlement = self.grant()

        self.assertEqual(entitlement.origin_type, INTERNAL_TEAM_ORIGIN)
        self.assertEqual(entitlement.status, InternetEntitlement.Status.ACTIVE)
        self.assertEqual(entitlement.bandwidth_profile_code, 'fast')
        self.assertEqual(entitlement.total_minutes_allowed, 1200)
        self.assertEqual(entitlement.daily_minutes_limit, 240)
        self.assertEqual(entitlement.max_concurrent_devices, 2)
        self.assertEqual(entitlement.max_registered_devices, 4)
        self.assertEqual(entitlement.network_backend, 'manual')
        self.assertEqual(entitlement.gross_amount_syp, 0)
        self.assertIsNone(entitlement.member_id)
        self.assertIsNone(entitlement.package_id)
        self.assertIsNone(entitlement.order_id)
        self.assertIsNone(entitlement.payment_id)
        self.assertIsNone(entitlement.partner_id)
        target = InternalStaffInternetGrant.objects.get(entitlement=entitlement)
        self.assertEqual(target.user, self.staff_user)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(InternetRevenueShare.objects.count(), 0)
        self.assertTrue(InternetNetworkOperation.objects.filter(
            entitlement=entitlement,
            operation=InternetNetworkOperation.Operation.PROVISION,
        ).exists())
        log = ActivityLog.objects.get(action='internet.internal_access_granted')
        self.assertEqual(log.details['grant_kind'], 'team')
        self.assertEqual(log.details['staff_user_id'], self.staff_user.pk)

    def test_owner_grant_can_be_unlimited_with_validity_and_device_limits(self):
        entitlement = self.grant(
            grant_kind='owner',
            access_mode='unlimited',
            total_minutes_allowed=None,
            daily_minutes_limit=None,
            max_concurrent_devices=3,
            max_registered_devices=6,
        )

        self.assertEqual(entitlement.origin_type, INTERNAL_OWNER_ORIGIN)
        self.assertIsNone(entitlement.total_minutes_allowed)
        self.assertEqual(entitlement.max_concurrent_devices, 3)
        self.assertEqual(entitlement.max_registered_devices, 6)
        self.assertGreater(entitlement.valid_until, timezone.now() + timedelta(days=29))

    def test_overlapping_internal_grant_requires_revocation_first_for_staff_user(self):
        self.grant()

        with self.assertRaisesMessage(ValidationError, 'يوجد بالفعل وصول داخلي قائم لهذا الموظف'):
            self.grant(grant_kind='owner')

    def test_internet_provider_account_cannot_receive_internal_staff_grant(self):
        with self.assertRaisesMessage(ValidationError, 'حساب مزوّد الإنترنت'):
            self.grant(staff_user=self.provider_user)

    def test_existing_entitlement_engine_enforces_concurrent_device_limit(self):
        entitlement = self.grant(max_concurrent_devices=1, max_registered_devices=2)
        first = start_usage_session(entitlement, device_mac='AA:BB:CC:DD:EE:01')

        self.assertEqual(first.status, InternetSession.Status.ACTIVE)
        with self.assertRaisesMessage(ValidationError, 'تم بلوغ حد الأجهزة المتزامنة'):
            start_usage_session(entitlement, device_mac='AA:BB:CC:DD:EE:02')

    def test_revoke_uses_existing_lifecycle_without_creating_commercial_rows(self):
        entitlement = self.grant()
        start_usage_session(entitlement)

        revoked = revoke_internal_access(entitlement, actor=self.actor)

        revoked.refresh_from_db()
        self.assertEqual(revoked.status, InternetEntitlement.Status.CANCELLED)
        self.assertFalse(revoked.sessions.filter(status=InternetSession.Status.ACTIVE).exists())
        self.assertTrue(InternetNetworkOperation.objects.filter(
            entitlement=revoked,
            operation=InternetNetworkOperation.Operation.DISCONNECT,
        ).exists())
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(InternetRevenueShare.objects.count(), 0)
        log = ActivityLog.objects.get(action='internet.internal_access_revoked')
        self.assertEqual(log.details['staff_user_id'], self.staff_user.pk)

    def test_internal_entitlement_and_session_are_outside_provider_scope(self):
        partner = InternetPartner.objects.create(
            name='Provider', active=True, is_default=True, revenue_share_percent=20,
        )
        entitlement = self.grant()
        session = start_usage_session(entitlement)

        self.assertFalse(_provider_entitlements(partner).filter(pk=entitlement.pk).exists())
        self.assertFalse(_provider_sessions(partner).filter(pk=session.pk).exists())

    def test_staff_internal_access_page_uses_staff_users_not_members(self):
        self.client.force_login(self.actor)
        workspace = reverse('staff_internet_sale') + '?internal=1'

        page = self.client.get(workspace)
        self.assertEqual(page.status_code, 200)
        self.assertTemplateUsed(page, 'staff/internet_internal_access.html')
        self.assertContains(page, 'إنترنت الإدارة والفريق')
        self.assertContains(page, 'الموظف')
        self.assertContains(page, 'سلوى السيد')
        self.assertNotContains(page, self.customer_member.name_ar)
        self.assertNotContains(page, self.provider_user.username)

        response = self.client.post(reverse('staff_internet_sale'), {
            'internet_action': 'internal_grant',
            'staff_user': str(self.staff_user.pk),
            'grant_kind': 'team',
            'bandwidth_profile': str(self.profile.pk),
            'access_mode': 'allowance',
            'validity_value': '14',
            'validity_unit': 'days',
            'session_minutes_limit': '120',
            'total_minutes_allowed': '600',
            'daily_minutes_limit': '180',
            'max_concurrent_devices': '2',
            'max_registered_devices': '3',
        })
        self.assertRedirects(response, workspace)
        entitlement = InternetEntitlement.objects.get(origin_type=INTERNAL_TEAM_ORIGIN)
        self.assertIsNone(entitlement.member_id)
        self.assertEqual(entitlement.internal_staff_grant.user, self.staff_user)
        self.assertEqual(entitlement.gross_amount_syp, 0)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(InternetRevenueShare.objects.count(), 0)

        page = self.client.get(workspace)
        self.assertContains(page, 'سلوى السيد')
        self.assertContains(page, 'بار')

        revoked = self.client.post(reverse('staff_internet_sale'), {
            'internet_action': 'internal_revoke',
            'entitlement_id': str(entitlement.pk),
        })
        self.assertRedirects(revoked, workspace)
        entitlement.refresh_from_db()
        self.assertEqual(entitlement.status, InternetEntitlement.Status.CANCELLED)

    def test_staff_internet_page_links_internal_access_workspace(self):
        self.client.force_login(self.actor)

        response = self.client.get(reverse('staff_internet'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'إنترنت الإدارة والفريق')
        self.assertContains(response, reverse('staff_internet_sale') + '?internal=1')

    def test_staff_grant_does_not_attach_to_unrelated_customer_member(self):
        entitlement = self.grant()

        self.assertIsNone(entitlement.member_id)
        self.assertEqual(Member.objects.count(), 1)
        self.assertEqual(Member.objects.get(), self.customer_member)
        self.assertFalse(
            InternetEntitlement.objects.filter(
                member=self.customer_member,
                origin_type__in=(INTERNAL_OWNER_ORIGIN, INTERNAL_TEAM_ORIGIN),
            ).exists()
        )
