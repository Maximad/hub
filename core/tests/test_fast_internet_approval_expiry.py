"""Fast Internet consent expiry: Django-only, financial/network-neutral."""
from datetime import datetime, timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    ActivityLog, HubVisit, HubVisitBrowserCredential, InternetSession, Order,
    Payment, SystemSetting,
)
from core.services.fast_internet_approval import (
    approval_context, expiry_policy, expire_pending_approvals,
    pending_approvals_queryset, request_approval, review_approval,
)
from core.settings_helpers import get_system_settings


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False,
    BUSINESS_DAY_CUTOFF_HOUR=4,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class FastInternetExpiryTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username='expiry-admin', phone='095870001', role='admin')
        self.cashier = get_user_model().objects.create_user(
            username='expiry-cashier', phone='095870002', role='cashier')
        self.settings_obj = SystemSetting.objects.create(
            customer_visits_enabled=True,
            customer_internet_self_service_enabled=True,
            fast_internet_staff_approval_required=True,
            fast_internet_approval_timeout_minutes=30,
        )
        self.visit = HubVisit.objects.create(
            guest_name='زائر موافقة', guest_phone='0991234000')
        self.device = HubVisitBrowserCredential.objects.create(
            visit=self.visit, token_hash='d' * 64,
            fast_internet_approval='pending',
            fast_internet_requested_at=timezone.now() - timedelta(minutes=31),
        )
        get_system_settings.cache_clear()
        self.client.force_login(self.cashier)

    def tearDown(self):
        get_system_settings.cache_clear()

    def test_overdue_device_hidden_from_both_staff_queues_without_get_mutations(self):
        before = ActivityLog.objects.count()
        response = self.client.get(reverse('staff_home'))
        self.assertEqual(response.context['staff_attention']['internet_count'], 0)
        self.assertEqual(pending_approvals_queryset().count(), 0)
        internet = self.client.get(reverse('staff_internet'))
        self.assertEqual(internet.wsgi_request.fast_internet_requests.paginator.count, 0)
        self.assertEqual(approval_context(self.device)['fast_approval_state'], 'expired')
        self.device.refresh_from_db()
        self.assertEqual(self.device.fast_internet_approval, 'pending')
        self.assertEqual(ActivityLog.objects.count(), before)

    def test_worker_records_expired_once_and_never_posts_billable_records(self):
        self.assertEqual(expire_pending_approvals(dry_run=True), 1)
        self.assertEqual(expire_pending_approvals(), 1)
        self.assertEqual(expire_pending_approvals(), 0)
        self.device.refresh_from_db()
        self.assertEqual(self.device.fast_internet_approval, 'expired')
        self.assertEqual(ActivityLog.objects.filter(
            action='internet.fast_approval_expired',
            details__credential_id=self.device.pk).count(), 1)
        self.assertEqual((Order.objects.count(), Payment.objects.count(),
                          InternetSession.objects.count()), (0, 0, 0))
        self.assertIsNone(self.device.fast_internet_reviewed_by)

    def test_worker_command_dry_run_and_without_open_business_day(self):
        output = StringIO()
        call_command('business_day_tick', '--dry-run', stdout=output)
        self.assertIn('fast_approvals_would_expire=1', output.getvalue())
        self.device.refresh_from_db()
        self.assertEqual(self.device.fast_internet_approval, 'pending')
        output = StringIO()
        call_command('business_day_tick', stdout=output)
        self.assertIn('fast_approvals_expired=1', output.getvalue())
        self.device.refresh_from_db()
        self.assertEqual(self.device.fast_internet_approval, 'expired')

    def test_expiry_at_cutoff_even_for_request_less_than_thirty_minutes_old(self):
        tz = timezone.get_current_timezone()
        tomorrow = timezone.localdate() + timedelta(days=1)
        request_at = timezone.make_aware(datetime.combine(tomorrow, datetime.min.time()), tz) + timedelta(hours=3, minutes=58)
        cutoff_at = request_at + timedelta(minutes=3)
        HubVisitBrowserCredential.objects.filter(pk=self.device.pk).update(
            fast_internet_approval='pending', fast_internet_requested_at=request_at,
            fast_internet_reviewed_at=None)
        self.assertEqual(expire_pending_approvals(at=request_at + timedelta(minutes=1), dry_run=True), 0)
        self.assertEqual(expire_pending_approvals(at=cutoff_at), 1)

    def test_stale_review_persists_expiration_and_rejects_late_staff_approval(self):
        with self.assertRaisesMessage(ValidationError, 'انتهت مهلة'):
            review_approval(credential_id=self.device.pk, actor=self.cashier, decision='approved')
        self.device.refresh_from_db()
        self.assertEqual(self.device.fast_internet_approval, 'expired')
        self.assertEqual(ActivityLog.objects.filter(action='internet.fast_approval_expired').count(), 1)
        self.assertFalse(ActivityLog.objects.filter(action='internet.fast_approval_reviewed').exists())

    def test_re_request_after_expiry_creates_fresh_pending_evidence(self):
        refreshed = request_approval(visit=self.visit, credential=self.device)
        self.assertEqual(refreshed.fast_internet_approval, 'pending')
        self.assertGreater(refreshed.fast_internet_requested_at,
                           timezone.now() - timedelta(minutes=1))
        self.assertIsNone(refreshed.fast_internet_reviewed_at)
        self.assertEqual(ActivityLog.objects.filter(action='internet.fast_approval_expired').count(), 1)
        self.assertEqual(ActivityLog.objects.filter(action='internet.fast_approval_requested').count(), 1)
        again = request_approval(visit=self.visit, credential=self.device)
        self.assertEqual(again.pk, self.device.pk)
        self.assertEqual(ActivityLog.objects.filter(action='internet.fast_approval_requested').count(), 1)
        reviewed = review_approval(credential_id=self.device.pk, actor=self.cashier, decision='approved')
        self.assertEqual(reviewed.fast_internet_approval, 'approved')

    def test_existing_rejected_requires_staff_reconsideration_and_approved_is_stable(self):
        HubVisitBrowserCredential.objects.filter(pk=self.device.pk).update(
            fast_internet_approval='rejected')
        self.assertEqual(request_approval(visit=self.visit, credential=self.device).fast_internet_approval, 'rejected')
        reviewed = review_approval(credential_id=self.device.pk, actor=self.cashier,
                                   decision='approved', expected_state='rejected')
        self.assertEqual(reviewed.fast_internet_approval, 'approved')
        self.assertEqual(expire_pending_approvals(), 0)

    def test_history_contains_expiry_even_after_device_re_request(self):
        expire_pending_approvals()
        request_approval(visit=self.visit, credential=self.device)
        response = self.client.get(reverse('staff_internet'))
        events = list(response.wsgi_request.fast_internet_history)
        actions = [event.action for event in events]
        self.assertIn('internet.fast_approval_expired', actions)
        self.assertIn('internet.fast_approval_requested', actions)
        self.assertContains(response, 'سجل طلبات الإنترنت السريع')
        self.assertContains(response, 'انتهت المهلة دون رد')

    def test_timeout_setting_can_be_changed_and_is_audited(self):
        self.client.force_login(self.admin)
        saved = self.client.post(reverse('staff_internet_settings'), {
            'settings_action': 'save_fast_approval_policy',
            'fast_internet_staff_approval_required': 'on',
            'fast_internet_approval_timeout_minutes': '60',
        })
        self.assertEqual(saved.status_code, 302)
        self.assertEqual(expiry_policy(), 60)
        self.assertTrue(ActivityLog.objects.filter(
            action='internet.fast_approval_policy_changed',
            details__timeout_minutes=60).exists())
        self.assertEqual(expire_pending_approvals(dry_run=True), 0)
        invalid = self.client.post(reverse('staff_internet_settings'), {
            'settings_action': 'save_fast_approval_policy',
            'fast_internet_staff_approval_required': 'on',
            'fast_internet_approval_timeout_minutes': '2',
        })
        self.assertEqual(invalid.status_code, 302)
        self.assertEqual(expiry_policy(), 60)

    def test_old_toggle_only_settings_post_keeps_existing_timeout(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('staff_internet_settings'), {
            'settings_action': 'save_fast_approval_policy'})
        self.assertEqual(expiry_policy(), 30)
        self.assertFalse(SystemSetting.objects.get(pk=self.settings_obj.pk).fast_internet_staff_approval_required)

    def test_history_not_exposed_to_unauthorized_staff(self):
        expire_pending_approvals()
        kitchen = get_user_model().objects.create_user(
            username='expiry-kitchen', phone='095870003', role='kitchen')
        self.client.force_login(kitchen)
        self.assertEqual(self.client.get(reverse('staff_internet')).status_code, 403)
        self.assertNotContains(self.client.get(reverse('staff_home')), 'data-attention-internet=')
