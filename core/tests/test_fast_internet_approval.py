"""Device approval gates new fast access without sales or router side effects."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserCapabilityOverride
from core.models import (
    ActivityLog, Category, HubVisit, HubVisitBrowserCredential, InternetEntitlement,
    InternetBandwidthProfile, InternetNetworkOperation, InternetPackage, InternetSession, Order, OrderItem, Payment,
    Product, Room, SystemSetting, TableArea,
)
from core.services import visit_internet as legacy
from core.services.fast_internet_approval import (
    APPROVAL, approval_required, request_approval, review_approval,
)
from core.services.visit_internet_devices import (
    create_visit_internet_sale_and_start, start_existing_visit_entitlement,
    start_visit_metered_session,
)
from core.services.visits import issue_visit_credential
from core.settings_helpers import get_system_settings
from internet.models import InternetSessionNetworkOperation


WEB = dict(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False, MIKROTIK_ENABLED=False,
           STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})


@override_settings(**WEB)
class FastInternetApprovalTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.cashier = User.objects.create_user(username='approval-cashier', phone='978001', role='cashier')
        self.admin = User.objects.create_user(username='approval-admin', phone='978002', role='admin')
        self.kitchen = User.objects.create_user(username='approval-kitchen', phone='978003', role='kitchen')
        category = Category.objects.create(name_ar='خدمات')
        product = Product.objects.create(category=category, name_ar='إنترنت', price_syp=0,
            product_type='internet', item_type='service', service_type='internet', requires_preparation=False)
        self.policy = SystemSetting.objects.create(customer_visits_enabled=True,
            customer_internet_self_service_enabled=True, default_rate_per_hour_syp=600,
            internet_service_product=product, auto_create_order_for_metered_sessions=True)
        self.visit = HubVisit.objects.create(table=TableArea.objects.create(
            room=Room.objects.create(name_ar='الصالة'), name_ar='طاولة 5'))
        self.credential, raw = issue_visit_credential(self.visit)
        self.client.cookies['hub_visit'] = raw
        self.package = InternetPackage.objects.create(name_ar='ساعة', code='approval-hour',
            price_syp=600, session_minutes_limit=60, visible_to_customer=True)
        get_system_settings.cache_clear()

    def tearDown(self):
        get_system_settings.cache_clear()

    def request(self):
        return request_approval(visit=self.visit, credential=self.credential)

    def review(self, decision='approved', **kwargs):
        return review_approval(credential_id=self.credential.pk, actor=self.cashier, decision=decision, **kwargs)

    def counts(self):
        return [model.objects.count() for model in (Order, Payment, InternetEntitlement,
            InternetSession, InternetNetworkOperation, InternetSessionNetworkOperation)]

    def test_default_is_on_and_request_retries_make_one_audited_pending_device(self):
        self.assertTrue(approval_required())
        before = self.counts()
        for _ in range(2):
            result = self.request()
            self.assertEqual(result.fast_internet_approval, 'pending')
        self.assertEqual(self.counts(), before)
        self.assertEqual(ActivityLog.objects.filter(action='internet.fast_approval_requested').count(), 1)
        self.assertIsNone(result.fast_internet_reviewed_at)

    def test_all_start_services_fail_before_sale_usage_or_network_jobs(self):
        from core.services.internet_access import create_entitlement
        entitlement = create_entitlement(self.package, visit=self.visit)
        before = self.counts()
        starts = [
            lambda: start_visit_metered_session(visit=self.visit, credential=self.credential),
            lambda: create_visit_internet_sale_and_start(visit=self.visit, credential=self.credential, package=self.package, request_key='attempt'),
            lambda: start_existing_visit_entitlement(visit=self.visit, credential=self.credential, entitlement=entitlement),
            lambda: legacy.start_visit_metered_session(visit=self.visit, credential=self.credential),
            lambda: legacy.create_visit_internet_sale_and_start(visit=self.visit, credential=self.credential, package=self.package, request_key='attempt'),
            lambda: legacy.start_existing_visit_entitlement(visit=self.visit, credential=self.credential, entitlement=entitlement),
        ]
        for start in starts:
            with self.subTest(start=start), self.assertRaisesMessage(ValidationError, 'موافقة الموظف'):
                start()
            self.assertEqual(self.counts(), before)

    def test_approval_is_recorded_once_then_device_starts_and_retries_once(self):
        self.request()
        before = self.counts()
        first = self.review()
        self.review()
        self.assertEqual(self.counts(), before)
        self.assertEqual(first.fast_internet_reviewed_by, self.cashier)
        self.assertEqual(ActivityLog.objects.filter(action='internet.fast_approval_reviewed').count(), 1)
        session, created = start_visit_metered_session(visit=self.visit, credential=self.credential)
        repeated, again = start_visit_metered_session(visit=self.visit, credential=self.credential)
        self.assertTrue(created)
        self.assertFalse(again)
        self.assertEqual(session.pk, repeated.pk)
        self.assertEqual(session.rate_per_hour_syp, 600)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(InternetSessionNetworkOperation.objects.count(), 1)

    def test_approval_does_not_authorize_other_browser_or_visit(self):
        self.request(); self.review()
        other, _ = issue_visit_credential(self.visit)
        with self.assertRaises(ValidationError):
            start_visit_metered_session(visit=self.visit, credential=other)
        different = HubVisit.objects.create(table=self.visit.table)
        with self.assertRaises(ValidationError):
            start_visit_metered_session(visit=different, credential=self.credential)
        self.assertEqual(InternetSession.objects.count(), 0)

    def test_off_switch_is_fresh_even_when_the_other_worker_cache_is_stale(self):
        self.assertTrue(get_system_settings().fast_internet_staff_approval_required)
        SystemSetting.objects.filter(pk=self.policy.pk).update(fast_internet_staff_approval_required=False)
        self.assertFalse(approval_required())
        session, _ = start_visit_metered_session(visit=self.visit, credential=self.credential)
        SystemSetting.objects.filter(pk=self.policy.pk).update(fast_internet_staff_approval_required=True)
        same, created = start_visit_metered_session(visit=self.visit, credential=self.credential)
        self.assertEqual(same.pk, session.pk)
        self.assertFalse(created)
        new_device, _ = issue_visit_credential(self.visit)
        with self.assertRaises(ValidationError):
            start_visit_metered_session(visit=self.visit, credential=new_device)

    def test_rejection_is_not_changed_by_stale_posts_or_customer_retries(self):
        self.request(); self.review('rejected')
        self.assertEqual(self.request().fast_internet_approval, 'rejected')
        with self.assertRaises(ValidationError):
            self.review()
        with self.assertRaises(ValidationError):
            start_visit_metered_session(visit=self.visit, credential=self.credential)
        self.review(expected_state='rejected')
        with self.assertRaises(ValidationError):
            self.review('rejected')
        self.credential.refresh_from_db()
        self.assertEqual(self.credential.fast_internet_approval, 'approved')

    def test_revoked_or_closed_visit_cannot_be_requested_approved_or_started(self):
        self.request()
        HubVisitBrowserCredential.objects.filter(pk=self.credential.pk).update(revoked_at=timezone.now())
        for call in (self.request, self.review, lambda: start_visit_metered_session(visit=self.visit, credential=self.credential)):
            with self.assertRaises(ValidationError): call()
        HubVisitBrowserCredential.objects.filter(pk=self.credential.pk).update(revoked_at=None)
        HubVisit.objects.filter(pk=self.visit.pk).update(status='closed')
        for call in (self.request, self.review, lambda: start_visit_metered_session(visit=self.visit, credential=self.credential)):
            with self.assertRaises(ValidationError): call()

    def test_unauthorized_review_and_revoked_capability_do_not_change_request(self):
        self.request()
        for actor in (self.kitchen, None):
            with self.assertRaises(PermissionDenied):
                review_approval(credential_id=self.credential.pk, actor=actor, decision='approved')
        UserCapabilityOverride.objects.create(user=self.cashier, capability='members/internet', allowed=False)
        with self.assertRaises(PermissionDenied): self.review()
        self.credential.refresh_from_db()
        self.assertEqual(self.credential.fast_internet_approval, 'pending')

    def test_failed_audit_rolls_back_request_and_review_then_retry_is_safe(self):
        with patch('core.services.fast_internet_approval.ActivityLog.objects.create', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError): self.request()
        self.credential.refresh_from_db()
        self.assertEqual(self.credential.fast_internet_approval, 'not_requested')
        self.request()
        with patch('core.services.fast_internet_approval.ActivityLog.objects.create', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError): self.review()
        self.credential.refresh_from_db()
        self.assertEqual(self.credential.fast_internet_approval, 'pending')
        self.assertIsNone(self.credential.fast_internet_reviewed_by)
        self.review()
        self.assertEqual(self.counts(), [0] * 6)

    def test_public_request_uses_cookie_not_posted_device_or_approval_values(self):
        other, _ = issue_visit_credential(self.visit)
        response = self.client.post(reverse('wifi_entry'), {'wifi_action': 'request_fast_approval',
            'credential_id': other.pk, 'fast_internet_approval': 'approved'})
        self.assertEqual(response.status_code, 302)
        self.credential.refresh_from_db(); other.refresh_from_db()
        self.assertEqual(self.credential.fast_internet_approval, 'pending')
        self.assertEqual(other.fast_internet_approval, 'not_requested')
        self.assertEqual(self.counts(), [0] * 6)
        for url, data in (
            (reverse('wifi_entry'), {'wifi_action': 'start_fast_wifi'}),
            (reverse('visit_internet_start'), {'mode': 'metered'}),
        ):
            with patch('core.views.wifi.prepare_visit_metered_session_network') as wifi_prepare, patch('core.views.visits.prepare_visit_metered_session_network') as visit_prepare:
                response = self.client.post(url, data)
                self.assertEqual(response.status_code, 302)
                wifi_prepare.assert_not_called(); visit_prepare.assert_not_called()
            self.assertEqual(self.counts(), [0] * 6)

    def test_standalone_request_requires_identity_and_failed_request_leaves_no_visit(self):
        client = Client()
        before = HubVisit.objects.count()
        self.assertEqual(client.post(reverse('wifi_entry'), {'wifi_action': 'request_fast_approval'}).status_code, 302)
        self.assertEqual(HubVisit.objects.count(), before)
        response = client.post(reverse('wifi_entry'), {'wifi_action': 'request_fast_approval', 'guest_name': 'زائر', 'guest_phone': '0991234567'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('hub_visit', response.cookies)
        created = HubVisit.objects.exclude(pk=self.visit.pk).get()
        self.assertEqual(created.guest_name, 'زائر')
        self.assertEqual(created.browser_credentials.get().fast_internet_approval, 'pending')
        self.assertEqual(self.counts(), [0] * 6)

    def test_staff_page_queue_and_post_only_review_preserve_customer_ownership(self):
        self.request()
        self.client.force_login(self.cashier)
        page = self.client.get(reverse('staff_internet'))
        self.assertContains(page, 'طلبات الإنترنت السريع')
        url = reverse('staff_fast_internet_approval', args=[self.credential.pk])
        self.assertContains(page, url)
        self.assertNotContains(page, self.credential.token_hash)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url, {'decision': 'approved'}).status_code, 302)
        self.assertEqual(self.counts(), [0] * 6)
        self.client.force_login(self.kitchen)
        self.assertEqual(self.client.post(url, {'decision': 'approved'}).status_code, 403)

    def test_settings_toggle_requires_settings_permission_and_changes_only_that_field(self):
        self.client.force_login(self.cashier)
        url = reverse('staff_internet_settings')
        data = {'settings_action': 'save_fast_approval_policy', 'default_rate_per_hour_syp': '1'}
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertTrue(approval_required())
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.policy.refresh_from_db()
        self.assertFalse(approval_required())
        self.assertEqual(self.policy.default_rate_per_hour_syp, 600)
        data['fast_internet_staff_approval_required'] = 'on'
        self.client.post(url, data)
        self.assertTrue(approval_required())
        self.assertEqual(ActivityLog.objects.filter(action='internet.fast_approval_policy_changed').count(), 2)

    def test_failed_staff_writes_return_safe_retry_messages_and_roll_back(self):
        self.request()
        self.client.force_login(self.cashier)
        url = reverse('staff_fast_internet_approval', args=[self.credential.pk])
        with patch('core.services.fast_internet_approval.ActivityLog.objects.create', side_effect=RuntimeError('private audit detail')), self.assertLogs('core.views.staff_internet', level='ERROR'):
            response = self.client.post(url, {'decision': 'approved'})
        self.assertEqual(response.status_code, 302)
        self.credential.refresh_from_db()
        self.assertEqual(self.credential.fast_internet_approval, 'pending')
        self.client.force_login(self.admin)
        with patch('core.views.internet_settings.ActivityLog.objects.create', side_effect=RuntimeError('private audit detail')), self.assertLogs('core.views.internet_settings', level='ERROR'):
            response = self.client.post(reverse('staff_internet_settings'), {'settings_action': 'save_fast_approval_policy'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(approval_required())
        self.assertFalse(ActivityLog.objects.filter(action='internet.fast_approval_policy_changed').exists())

    def test_pending_and_approved_customer_ui_offer_clear_next_action(self):
        self.request()
        for url in (reverse('wifi_entry') + '?mode=internet', reverse('current_visit') + '?focus=internet'):
            page = self.client.get(url)
            self.assertContains(page, 'بانتظار موافقة الموظف')
            self.assertContains(page, 'تحديث حالة الطلب')
            target = reverse('current_visit') if 'focus=' in url else reverse('wifi_entry')
            self.assertContains(page, f'<form method="get" action="{target}">')
            self.assertNotContains(page, 'value="start_fast_wifi"')
        self.review()
        page = self.client.get(reverse('current_visit') + '?focus=internet')
        self.assertContains(page, 'وافق الموظف')
        self.assertContains(page, 'value="metered"')

    def test_legacy_cart_fulfillment_cannot_bypass_device_approval(self):
        from internet.catalog import ensure_package_catalog_product, fulfill_internet_items_for_order
        product = ensure_package_catalog_product(self.package)
        order = Order.objects.create(visit=self.visit, table=self.visit.table, service_mode='table', fulfillment_mode='table')
        OrderItem.objects.create(order=order, product=product, quantity=1,
            product_name_ar_snapshot=product.name_ar, unit_price_syp_snapshot=600, line_total_syp_snapshot=600)
        before = self.counts()
        with self.assertRaises(ValidationError):
            fulfill_internet_items_for_order(order, credential=self.credential)
        self.assertEqual(self.counts(), before)
        self.request(); self.review()
        result = fulfill_internet_items_for_order(order, credential=self.credential)
        self.assertEqual(result[0][1].browser_binding.credential_id, self.credential.pk)

    def test_approval_post_is_csrf_protected(self):
        self.request()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.cashier)
        url = reverse('staff_fast_internet_approval', args=[self.credential.pk])
        self.assertEqual(client.post(url, {'decision': 'approved'}).status_code, 403)
        self.credential.refresh_from_db()
        self.assertEqual(self.credential.fast_internet_approval, 'pending')

    def test_basic_wifi_and_private_staff_access_keep_existing_policy(self):
        from internet.guest_wifi import get_guest_wifi_policy, start_guest_wifi_session
        from core.services.internet_internal_access import grant_internal_access
        policy = get_guest_wifi_policy()
        policy.enabled = True; policy.require_venue_code = False
        policy.save()
        with patch('core.views.wifi.prepare_guest_wifi_session_network', return_value=False):
            self.client.post(reverse('wifi_entry'), {'wifi_action': 'start_guest_wifi'})
        basic = InternetSession.objects.get()
        self.request()
        with self.assertRaises(ValidationError):
            start_visit_metered_session(visit=self.visit, credential=self.credential)
        basic.refresh_from_db()
        self.assertEqual(basic.status, 'active')
        grant = grant_internal_access(actor=self.admin, staff_user=self.cashier, grant_kind='team',
            bandwidth_profile=InternetBandwidthProfile.objects.create(code='approval-team', name='فريق'),
            access_mode=InternetPackage.AccessMode.ALLOWANCE, validity_value=1, validity_unit='days',
            total_minutes_allowed=60, daily_minutes_limit=60)
        # The caller must still prove the staff grant belongs to this staff user.
        with self.assertRaises(ValidationError):
            start_existing_visit_entitlement(visit=self.visit, credential=self.credential, entitlement=grant)

        internal, created = start_existing_visit_entitlement(visit=self.visit, credential=self.credential, entitlement=grant, staff_user=self.cashier)
        self.assertTrue(created)
        self.assertEqual(internal.entitlement_id, grant.pk)


@override_settings(**WEB)
class ConcurrentFastInternetApprovalTests(TransactionTestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(username='approval-concurrent', phone='978004', role='cashier')
        SystemSetting.objects.create(customer_visits_enabled=True, customer_internet_self_service_enabled=True)
        self.visit = HubVisit.objects.create(guest_name='زائر متزامن', guest_phone='0991234567')
        self.credential, _ = issue_visit_credential(self.visit)
        get_system_settings.cache_clear()

    def tearDown(self):
        get_system_settings.cache_clear()

    def parallel(self, operation):
        def run(_):
            close_old_connections()
            try:
                return operation()
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(run, range(2)))

    def test_concurrent_requests_create_one_pending_evidence(self):
        ids = self.parallel(lambda: request_approval(visit=self.visit, credential=self.credential).pk)
        self.assertEqual(ids, [self.credential.pk] * 2)
        self.assertEqual(ActivityLog.objects.filter(action='internet.fast_approval_requested').count(), 1)
        self.assertEqual(InternetSession.objects.count(), 0)

    def test_concurrent_approvals_record_only_one_review(self):
        request_approval(visit=self.visit, credential=self.credential)
        def approve():
            actor = get_user_model().objects.get(pk=self.actor.pk)
            return review_approval(credential_id=self.credential.pk, actor=actor, decision='approved').pk
        self.assertEqual(self.parallel(approve), [self.credential.pk] * 2)
        self.assertEqual(ActivityLog.objects.filter(action='internet.fast_approval_reviewed').count(), 1)
        self.assertEqual(InternetSession.objects.count(), 0)
