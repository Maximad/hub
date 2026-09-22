from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import (
    Category,
    HubVisit,
    InternetBandwidthProfile,
    InternetEntitlement,
    InternetPartner,
    InternetRevenueShare,
    InternetSession,
    Member,
    Order,
    OrderItem,
    Payment,
    Product,
    SystemSetting,
)
from core.services.internet_access import start_usage_session
from core.services.internet_internal_access import grant_internal_access, revoke_internal_access
from core.services.mikrotik import RouterOSClient
from core.settings_helpers import get_system_settings
from core.views.internet_provider import _provider_entitlements, _provider_members, _provider_sessions
from internet.guest_wifi import current_venue_code, guest_wifi_daily_minutes_remaining
from internet.models import GuestWifiDailyAllowance, GuestWifiPolicy


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    MIKROTIK_ENABLED=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class InternetLaunchContractTests(TestCase):
    """Cross-system guarantees required before Internet rollout.

    Focused unit tests remain the detailed source of truth. These tests intentionally
    exercise only the product-level boundaries that must remain true together.
    """

    def tearDown(self):
        get_system_settings.cache_clear()

    def _enable_self_service(self, *, require_code=True):
        SystemSetting.objects.create(
            customer_visits_enabled=True,
            customer_internet_self_service_enabled=True,
        )
        get_system_settings.cache_clear()
        policy = GuestWifiPolicy.objects.get(key='default')
        policy.enabled = True
        policy.require_venue_code = require_code
        policy.session_minutes = 120
        policy.daily_complimentary_minutes = 360
        policy.order_bonus_enabled = True
        policy.order_bonus_minutes = 120
        policy.qualifying_order_minimum_syp = 100
        policy.save()
        return policy

    def test_menu_and_internet_discovery_are_non_billable_and_do_not_start_access(self):
        self._enable_self_service(require_code=True)

        landing = self.client.get(reverse('wifi_entry'))
        menu = self.client.get(reverse('menu_public'))
        internet = self.client.get(reverse('wifi_entry'), {'mode': 'internet'})

        self.assertEqual(landing.status_code, 200)
        self.assertEqual(menu.status_code, 200)
        self.assertEqual(internet.status_code, 200)
        self.assertContains(landing, 'افتح المنيو واطلب')
        self.assertContains(internet, 'name="venue_code"')
        self.assertEqual(HubVisit.objects.count(), 0)
        self.assertEqual(InternetSession.objects.count(), 0)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)

    @override_settings(MIKROTIK_ENABLED=False)
    def test_basic_pin_allowance_and_order_bonus_remain_bounded(self):
        policy = self._enable_self_service(require_code=True)

        bad = self.client.post(reverse('wifi_entry'), {
            'wifi_action': 'start_guest_wifi',
            'venue_code': '0000',
        })
        self.assertEqual(bad.status_code, 302)
        self.assertEqual(InternetSession.objects.count(), 0)
        self.assertFalse(GuestWifiDailyAllowance.objects.filter(initial_minutes_granted__gt=0).exists())

        with self.settings(MIKROTIK_ENABLED=False):
            good = self.client.post(reverse('wifi_entry'), {
                'wifi_action': 'start_guest_wifi',
                'venue_code': current_venue_code(policy),
            })
        self.assertEqual(good.status_code, 302)
        session = InternetSession.objects.get(status=InternetSession.Status.ACTIVE)
        self.assertEqual(session.billing_mode, InternetSession.BillingMode.FREE)
        allowance = GuestWifiDailyAllowance.objects.get()
        remaining_before = guest_wifi_daily_minutes_remaining(allowance.credential, policy)

        category = Category.objects.create(name_ar='اختبار الإطلاق')
        product = Product.objects.create(category=category, name_ar='طلب مؤهل', price_syp=200)
        order = Order.objects.create(visit=allowance.credential.visit, status=Order.Status.NEW)
        OrderItem.objects.create(
            order=order,
            product=product,
            quantity=1,
            product_name_ar_snapshot=product.name_ar,
            unit_price_syp_snapshot=200,
            line_total_syp_snapshot=200,
        )
        order.status = Order.Status.ACCEPTED
        order.save(update_fields=['status', 'updated_at'])

        allowance.refresh_from_db()
        remaining_after = guest_wifi_daily_minutes_remaining(allowance.credential, policy)
        self.assertGreater(allowance.order_bonus_minutes_granted, 0)
        self.assertGreater(remaining_after, remaining_before)
        self.assertLessEqual(allowance.total_granted_minutes, policy.daily_complimentary_minutes)

    def test_internal_access_is_complimentary_bounded_and_provider_private(self):
        actor = get_user_model().objects.create_superuser(
            username='launch-admin', password='pass', email='launch@example.com', phone='+963900001234',
        )
        member = Member.objects.create(name_ar='فريق هَبّ', phone='+963900005678')
        profile = InternetBandwidthProfile.objects.create(
            code='launch-fast', name='Launch Fast', router_profile_name='hub-full', is_active=True,
        )
        partner = InternetPartner.objects.create(
            name='Launch ISP', active=True, is_default=True, revenue_share_percent=20,
        )

        entitlement = grant_internal_access(
            member=member,
            grant_kind='team',
            bandwidth_profile=profile,
            access_mode='allowance',
            validity_value=7,
            validity_unit='days',
            session_minutes_limit=120,
            total_minutes_allowed=600,
            daily_minutes_limit=180,
            max_concurrent_devices=1,
            max_registered_devices=2,
            actor=actor,
        )
        first = start_usage_session(entitlement, device_mac='AA:BB:CC:DD:EE:01')
        self.assertEqual(first.status, InternetSession.Status.ACTIVE)
        with self.assertRaisesMessage(Exception, 'تم بلوغ حد الأجهزة المتزامنة'):
            start_usage_session(entitlement, device_mac='AA:BB:CC:DD:EE:02')

        self.assertEqual(entitlement.gross_amount_syp, 0)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(InternetRevenueShare.objects.count(), 0)
        self.assertFalse(_provider_entitlements(partner).filter(pk=entitlement.pk).exists())
        self.assertFalse(_provider_sessions(partner).filter(pk=first.pk).exists())
        self.assertFalse(_provider_members(partner).filter(pk=member.pk).exists())

        revoke_internal_access(entitlement, actor=actor)
        entitlement.refresh_from_db()
        first.refresh_from_db()
        self.assertEqual(entitlement.status, InternetEntitlement.Status.CANCELLED)
        self.assertNotEqual(first.status, InternetSession.Status.ACTIVE)

    def test_django_has_no_router_configuration_writer_contract(self):
        forbidden_methods = (
            'create_profile',
            'update_profile',
            'create_walled_garden',
            'update_walled_garden',
        )
        for method in forbidden_methods:
            self.assertFalse(hasattr(RouterOSClient, method), method)

        actor = get_user_model().objects.create_superuser(
            username='boundary-admin', password='pass', email='boundary@example.com', phone='+963900009999',
        )
        self.client.force_login(actor)
        response = self.client.post(reverse('staff_internet_settings'), {
            'operation_action': 'sync_mikrotik_portal_policy',
        })
        self.assertEqual(response.status_code, 302)
