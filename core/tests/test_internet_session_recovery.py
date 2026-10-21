"""Staff recovery reuses durable jobs and never creates commercial records."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserCapabilityOverride
from core.models import ActivityLog, InternetSession, Order, Payment
from core.services.internet_operations import requeue_failed_network_operation
from core.services.internet_session_recovery import session_recovery_context
from core.tests.test_metered_safe_start import SuccessfulBackend
from internet.models import InternetSessionNetworkOperation as Job, InternetSessionNetworkState
from internet.session_network_operations import process_session_network_operation

WEB = dict(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False, MIKROTIK_ENABLED=False,
           STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})


@override_settings(**WEB)
class InternetSessionRecoveryTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(username='recovery-admin', phone='979001', role='admin')
        self.cashier = User.objects.create_user(username='recovery-cashier', phone='979002', role='cashier')
        self.session = InternetSession.objects.create(start_time=timezone.now() - timedelta(hours=1),
            network_provider='mikrotik', network_status='provision_error', rate_per_hour_syp=600,
            minimum_minutes=30, minimum_charge_syp=300)
        self.job = Job.objects.create(session=self.session, operation='provision', status='failed',
            idempotency_key='recovery-test', attempt_count=2,
            last_error='RAW-PRIVATE router response password=secret-value',
            next_attempt_at=timezone.now() + timedelta(minutes=15))
        self.detail = reverse('staff_internet_session', args=[self.session.pk])
        self.retry = reverse('staff_internet_session_retry', args=[self.session.pk])
        self.client.force_login(self.admin)

    def counts(self):
        return InternetSession.objects.count(), Job.objects.count(), Order.objects.count(), Payment.objects.count()

    def test_waiting_page_is_readonly_safe_and_does_not_preview_charge(self):
        before = self.counts()
        response = self.client.get(self.detail)
        self.assertContains(response, 'لم يبدأ احتساب الوقت لأن تفعيل الشبكة لم يكتمل.')
        self.assertContains(response, 'إعادة محاولة تجهيز الاتصال')
        self.assertNotContains(response, 'RAW-PRIVATE')
        self.assertNotContains(response, 'secret-value')
        self.assertNotContains(response, 'معاينة الإنهاء الآن')
        self.assertEqual(response.context['preview']['calculated_total'], 0)
        self.assertEqual(response.context['preview']['billable_minutes'], 0)
        self.assertEqual(self.counts(), before)
        self.assertFalse(ActivityLog.objects.filter(action='internet.network_operation_requeued').exists())
        end = self.client.get(reverse('staff_internet_end', args=[self.session.pk]))
        self.assertContains(end, 'راجع حالة الاتصال قبل الإنهاء')
        self.assertNotContains(end, 'تأكيد الإنهاء')

    def test_retry_reuses_same_job_and_is_duplicate_safe_without_network_io(self):
        before = self.counts()
        with patch('internet.session_network_operations.get_session_network_backend') as backend:
            response = self.client.post(self.retry, {'operation_id': self.job.pk})
            duplicate = self.client.post(self.retry, {'operation_id': self.job.pk}, follow=True)
        self.assertRedirects(response, self.detail)
        self.assertContains(duplicate, 'يمكن إعادة محاولة العمليات الفاشلة فقط.')
        backend.assert_not_called()
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'pending')
        self.assertEqual(self.job.attempt_count, 2)
        self.assertIsNone(self.job.next_attempt_at)
        self.assertEqual(self.counts(), before)
        self.assertEqual(ActivityLog.objects.filter(action='internet.network_operation_requeued').count(), 1)
        page = self.client.get(self.detail)
        self.assertContains(page, 'العملية في قائمة العامل')
        self.assertNotContains(page, 'إعادة محاولة تجهيز الاتصال')

    def test_cashier_can_view_but_cannot_retry_even_with_forged_post(self):
        self.client.force_login(self.cashier)
        page = self.client.get(self.detail)
        self.assertContains(page, 'حالة الاتصال والاحتساب')
        self.assertNotContains(page, 'إعادة محاولة تجهيز الاتصال')
        self.client.post(self.retry, {'operation_id': self.job.pk})
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'failed')

    def test_revoked_permission_prevents_retry(self):
        UserCapabilityOverride.objects.create(user=self.admin, capability='settings', allowed=False)
        self.client.post(self.retry, {'operation_id': self.job.pk})
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'failed')

    def test_settings_permission_alone_does_not_grant_session_access(self):
        UserCapabilityOverride.objects.create(user=self.admin, capability='members/internet', allowed=False)
        before = self.counts()
        self.client.post(self.retry, {'operation_id': self.job.pk})
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'failed')
        self.assertEqual(self.counts(), before)

    def test_retry_requires_post_authentication_and_csrf(self):
        self.assertEqual(self.client.get(self.retry).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post(self.retry, {'operation_id': self.job.pk}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(self.retry, {'operation_id': self.job.pk}).status_code, 302)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'failed')

    def test_foreign_invalid_and_missing_job_ids_cannot_change_any_job(self):
        other = InternetSession.objects.create(start_time=timezone.now())
        foreign = Job.objects.create(session=other, operation='provision', status='failed', idempotency_key='foreign')
        for value in (foreign.pk, 99999999, 'bad', ''):
            response = self.client.post(self.retry, {'operation_id': value}, follow=True)
            self.assertEqual(response.status_code, 200)
        foreign.refresh_from_db()
        self.job.refresh_from_db()
        self.assertEqual(foreign.status, 'failed')
        self.assertEqual(self.job.status, 'failed')

    def test_terminal_session_cannot_reactivate_but_disconnect_retry_is_available(self):
        for state in ('ended', 'cancelled', 'billed', 'paid'):
            self.session.status = state
            self.session.save(update_fields=['status'])
            page = self.client.post(self.retry, {'operation_id': self.job.pk}, follow=True)
            self.assertContains(page, 'لا يمكن إعادة تفعيل جلسة منتهية أو ملغاة.')
            self.assertNotContains(page, 'إعادة محاولة تجهيز الاتصال')
            self.job.refresh_from_db()
            self.assertEqual(self.job.status, 'failed')
        # The original settings entry point must enforce the same lifecycle guard.
        self.client.post(reverse('staff_internet_settings'), {
            'operation_action': 'retry_network_operation', 'operation_kind': 'session',
            'operation_id': self.job.pk,
        })
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'failed')
        self.job.operation = 'disconnect'
        self.job.save(update_fields=['operation'])
        self.client.post(self.retry, {'operation_id': self.job.pk})
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'pending')

    def test_audit_failure_rolls_back_retry_and_returns_safe_error(self):
        before = self.counts()
        original_attempt = self.job.next_attempt_at
        with patch('core.services.internet_operations.ActivityLog.objects.create', side_effect=RuntimeError('PRIVATE write detail')), self.assertLogs('core.views.staff_internet', level='ERROR'):
            response = self.client.post(self.retry, {'operation_id': self.job.pk}, follow=True)
        self.assertContains(response, 'تعذر حفظ إعادة المحاولة.')
        self.assertNotContains(response, 'PRIVATE write detail')
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'failed')
        self.assertEqual(self.job.next_attempt_at, original_attempt)
        self.assertEqual(self.counts(), before)

    def test_worker_success_anchors_clock_once_without_sale_or_payment(self):
        self.client.post(self.retry, {'operation_id': self.job.pk})
        before = self.counts()
        with patch('internet.session_network_operations.get_session_network_backend', return_value=SuccessfulBackend()):
            self.assertTrue(process_session_network_operation(self.job.pk))
            self.assertFalse(process_session_network_operation(self.job.pk))
        state = InternetSessionNetworkState.objects.get(session=self.session)
        self.assertIsNotNone(state.network_activated_at)
        self.session.refresh_from_db()
        self.assertEqual(self.session.start_time, state.network_activated_at)
        self.assertEqual(self.counts(), before)
        page = self.client.get(self.detail)
        self.assertContains(page, 'بدأ احتساب الوقت عند نجاح التفعيل.')
        self.assertContains(page, 'جاهز للاتصال')
        self.assertContains(page, 'جاهزية الوصول لا تثبت اتصال جهاز الزبون فعلياً.')
        # Refresh/reprovision must not reset an already activated billing clock.
        self.job.refresh_from_db()
        self.assertEqual(self.job.attempt_count, 3)
        refresh = Job.objects.create(session=self.session, operation='refresh', status='pending',
                                     idempotency_key='recovery-refresh')
        with patch('internet.session_network_operations.get_session_network_backend', return_value=SuccessfulBackend()):
            self.assertTrue(process_session_network_operation(refresh.pk))
        self.assertEqual(InternetSessionNetworkState.objects.get(session=self.session).network_activated_at, state.network_activated_at)

    def test_failed_worker_keeps_billing_gate_closed_and_same_job(self):
        self.client.post(self.retry, {'operation_id': self.job.pk})
        before = self.counts()
        with patch('internet.session_network_operations.get_session_network_backend') as backend:
            backend.return_value.provision_access.side_effect = RuntimeError('temporary connection error')
            self.assertFalse(process_session_network_operation(self.job.pk))
        self.job.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual(self.job.status, 'failed')
        self.assertFalse(session_recovery_context(self.session)['billing_ready'])
        self.assertEqual(self.counts(), before)

    def test_manual_and_free_sessions_keep_existing_billing_presentation(self):
        self.session.start_time = timezone.now() - timedelta(minutes=40)
        self.session.started_at = self.session.start_time
        self.session.network_provider = 'manual'
        self.session.network_status = ''
        self.session.save()
        page = self.client.get(self.detail)
        self.assertContains(page, 'اتصال يُدار يدوياً')
        self.assertContains(page, 'معاينة الإنهاء الآن')
        self.assertEqual(page.context['preview']['calculated_total'], 450)
        self.session.billing_mode = 'free'
        self.session.save()
        self.assertContains(self.client.get(self.detail), 'هذه جلسة مجانية.')


@override_settings(**WEB)
class InternetSessionRecoveryConcurrencyTests(TransactionTestCase):
    def test_concurrent_retries_queue_and_audit_only_once(self):
        session = InternetSession.objects.create(start_time=timezone.now(), network_provider='mikrotik')
        job = Job.objects.create(session=session, operation='provision', status='failed', idempotency_key='concurrent-recovery')

        def retry(_):
            close_old_connections()
            try:
                requeue_failed_network_operation(kind='session', operation_id=job.pk, session_id=session.pk)
                return True
            except ValidationError:
                return False
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(retry, range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(Job.objects.count(), 1)
        self.assertEqual(ActivityLog.objects.filter(action='internet.network_operation_requeued').count(), 1)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)
