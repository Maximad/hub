"""Read-only, capability-filtered Operations attention inbox regressions."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserCapabilityOverride
from core.models import ActivityLog, HubVisit, HubVisitBrowserCredential, SystemSetting
from core.services.staff_attention import get_staff_attention
from operations.models import BusinessDay, BusinessDayTask, OperationalTaskTemplate
from operations.services import current_business_date


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class UnifiedStaffAttentionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='unified-admin', phone='0946655001', role='admin')
        self.cashier = User.objects.create_user(
            username='unified-cashier', phone='0946655002', role='cashier')
        self.waiter = User.objects.create_user(
            username='unified-waiter', phone='0946655003', role='waiter')
        self.kitchen = User.objects.create_user(
            username='unified-kitchen', phone='0946655004', role='kitchen')
        self.day = BusinessDay.objects.create(
            business_date=current_business_date(), status=BusinessDay.Status.OPEN)
        self.setting = SystemSetting.objects.create(
            fast_internet_staff_approval_required=True)
        self.visit = HubVisit.objects.create(guest_name='زائر الاختبار')
        self.credential = HubVisitBrowserCredential.objects.create(
            visit=self.visit, token_hash='a' * 64,
            fast_internet_approval=HubVisitBrowserCredential.FastInternetApproval.PENDING,
            fast_internet_requested_at=timezone.now())
        self.client.force_login(self.cashier)

    def task(self, *, fingerprint, title='تحضير القاعة', priority='high',
             status='pending', assigned_to=None, role='', required=False,
             due_at=None, source_type=''):
        return BusinessDayTask.objects.create(
            business_day=self.day, fingerprint=fingerprint, title_ar=title,
            priority=priority, status=status, assigned_to=assigned_to,
            responsibility_role=role, is_required=required, due_at=due_at,
            source_type=source_type)

    def test_pending_approval_and_high_task_are_actionable_without_creating_records(self):
        task = self.task(fingerprint='urgent', assigned_to=self.cashier)
        before = (HubVisitBrowserCredential.objects.count(), BusinessDayTask.objects.count())
        response = self.client.get(reverse('staff_home'))
        self.assertEqual(response.status_code, 200)
        inbox = response.context['staff_attention']
        self.assertEqual(inbox['internet_count'], 1)
        self.assertEqual(inbox['tasks_count'], 1)
        self.assertEqual(inbox['tasks'][0].pk, task.pk)
        self.assertContains(response, 'data-attention-internet="%s"' % self.credential.pk)
        self.assertContains(response, 'data-attention-task="%s"' % task.pk)
        self.assertContains(response, 'موافقة الإنترنت السريع')
        self.assertContains(response, 'تحتاج متابعة الآن')
        self.assertEqual(before, (
            HubVisitBrowserCredential.objects.count(), BusinessDayTask.objects.count()))

    def test_approving_inline_returns_to_operations_and_removes_request(self):
        url = reverse('staff_fast_internet_approval', args=[self.credential.pk])
        response = self.client.post(url, {
            'decision': 'approved', 'return_to': 'operations'})
        self.assertRedirects(response, reverse('staff_home'), fetch_redirect_response=False)
        self.credential.refresh_from_db()
        self.assertEqual(self.credential.fast_internet_approval, 'approved')
        self.assertEqual(self.credential.fast_internet_reviewed_by, self.cashier)
        self.assertEqual(ActivityLog.objects.filter(
            action='internet.fast_approval_reviewed').count(), 1)
        self.assertEqual(self.client.get(reverse('staff_home')).context[
            'staff_attention']['internet_count'], 0)

    def test_completing_inline_returns_to_operations_and_removes_task(self):
        task = self.task(fingerprint='complete')
        response = self.client.post(reverse('staff_operational_tasks'), {
            'task_action': 'status', 'task_id': task.pk, 'status': 'done',
            'return_to': 'operations'})
        self.assertRedirects(response, reverse('staff_home'), fetch_redirect_response=False)
        task.refresh_from_db()
        self.assertEqual(task.status, BusinessDayTask.Status.DONE)
        self.assertEqual(task.completed_by, self.cashier)
        self.assertFalse(any(row.pk == task.pk for row in
                             self.client.get(reverse('staff_home')).context[
                                 'staff_attention']['tasks']))

    def test_kitchen_cannot_see_or_decide_internet_approvals(self):
        task = self.task(fingerprint='kitchen', role='kitchen')
        self.client.force_login(self.kitchen)
        response = self.client.get(reverse('staff_home'))
        self.assertEqual(response.context['staff_attention']['internet_count'], 0)
        self.assertNotContains(response, f'data-attention-internet="{self.credential.pk}"')
        self.assertEqual(response.context['staff_attention']['tasks'][0].pk, task.pk)
        forbidden = self.client.post(reverse('staff_fast_internet_approval',
                                             args=[self.credential.pk]),
                                     {'decision': 'approved', 'return_to': 'operations'})
        self.assertEqual(forbidden.status_code, 403)
        self.credential.refresh_from_db()
        self.assertEqual(self.credential.fast_internet_approval, 'pending')

    def test_permission_override_hides_internet_data_even_from_cashier(self):
        UserCapabilityOverride.objects.create(
            user=self.cashier, capability='members/internet', allowed=False)
        response = self.client.get(reverse('staff_home'))
        self.assertEqual(response.context['staff_attention']['internet_count'], 0)
        self.assertNotContains(response, f'data-attention-internet="{self.credential.pk}"')

    def test_switch_off_and_closed_or_revoked_visits_are_not_live_attention(self):
        SystemSetting.objects.filter(pk=self.setting.pk).update(
            fast_internet_staff_approval_required=False)
        response = self.client.get(reverse('staff_home'))
        self.assertEqual(response.context['staff_attention']['internet_count'], 0)
        SystemSetting.objects.filter(pk=self.setting.pk).update(
            fast_internet_staff_approval_required=True)
        self.visit.status = HubVisit.Status.CLOSED
        self.visit.closed_at = timezone.now()
        self.visit.save(update_fields=['status', 'closed_at', 'updated_at'])
        self.assertEqual(self.client.get(reverse('staff_home')).context[
            'staff_attention']['internet_count'], 0)
        self.visit.status = HubVisit.Status.OPEN
        self.visit.save(update_fields=['status', 'updated_at'])
        self.credential.revoked_at = timezone.now()
        self.credential.save(update_fields=['revoked_at'])
        self.assertEqual(self.client.get(reverse('staff_home')).context[
            'staff_attention']['internet_count'], 0)

    def test_private_and_irrelevant_tasks_are_not_shown_to_waiter(self):
        mine = self.task(fingerprint='waiter-role', role='waiter')
        another = self.task(fingerprint='assigned-other', assigned_to=self.cashier,
                            role='cashier')
        manager_only = self.task(fingerprint='private-review',
                                 source_type='OperationalReviewCase')
        normal = self.task(fingerprint='normal', priority='normal')
        completed = self.task(fingerprint='completed', status='done', role='waiter')
        self.client.force_login(self.waiter)
        response = self.client.get(reverse('staff_home'))
        visible = [t.pk for t in response.context['staff_attention']['tasks']]
        self.assertEqual(visible, [mine.pk])
        for task in (another, manager_only, normal, completed):
            self.assertNotIn(task.pk, visible)
            self.assertNotContains(response, f'data-attention-task="{task.pk}"')
        denied = self.client.post(reverse('staff_operational_tasks'), {
            'task_action': 'status', 'task_id': another.pk, 'status': 'done',
            'return_to': 'operations'})
        self.assertEqual(denied.status_code, 403)

    def test_overdue_first_and_counts_not_capped_and_closed_day_clears_tasks(self):
        for n in range(6):
            self.task(fingerprint=f'high-{n}', priority='high')
        overdue = self.task(fingerprint='late', priority='normal',
                            due_at=timezone.now()-timedelta(hours=2))
        resp = self.client.get(reverse('staff_home'))
        inbox = resp.context['staff_attention']
        self.assertEqual(inbox['tasks_count'], 7)
        self.assertTrue(inbox['more_tasks'])
        self.assertEqual(len(inbox['tasks']), 5)
        self.assertEqual(inbox['tasks'][0].pk, overdue.pk)
        self.assertTrue(inbox['tasks'][0].attention_overdue)
        self.day.status = BusinessDay.Status.CLOSED
        self.day.save(update_fields=['status', 'updated_at'])
        self.assertEqual(self.client.get(reverse('staff_home')).context[
            'staff_attention']['tasks_count'], 0)

    def test_approvals_paginated_in_dashboard_and_legacy_route_unchanged(self):
        for i in range(3):
            HubVisitBrowserCredential.objects.create(
                visit=self.visit, token_hash=f'{i+2:064x}',
                fast_internet_approval='pending',
                fast_internet_requested_at=timezone.now())
        response = self.client.get(reverse('staff_home'))
        att = response.context['staff_attention']
        self.assertEqual(att['internet_count'], 4)
        self.assertTrue(att['more_internet'])
        self.assertEqual(len(att['internet_requests']), 3)
        self.assertEqual(response['Cache-Control'], 'private, no-store, max-age=0')
        legacy = self.client.post(reverse('staff_fast_internet_approval',
                                          args=[self.credential.pk]), {'decision': 'rejected'})
        self.assertRedirects(legacy, reverse('staff_internet'),
                             fetch_redirect_response=False)

    def test_no_open_business_day_does_not_resurrect_old_task(self):
        self.task(fingerprint='old')
        self.day.status = BusinessDay.Status.CLOSED
        self.day.save(update_fields=['status','updated_at'])
        self.assertEqual(self.client.get(reverse('staff_home')).context[
            'staff_attention']['tasks_count'], 0)
