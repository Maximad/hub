"""One source-backed follow-up can be completed by its authorized duty holder."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch
import time

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserCapabilityOverride
from core.models import (
    ActivityLog, Category, InventoryItem, Payment, PostingBatch, Product,
    Purchase, StockMovement,
)
from operations.duty import assign_duty_coverage, request_duty_handover, accept_duty_handover
from operations.models import BusinessDay, BusinessDayTask, OperationalReviewCase
from operations.opening import set_staff_roster
from operations.review_cases import (
    confirm_case_recheck, preview_case_recheck, review_presentation, run_review_sync,
)
from operations.services import open_business_day


class ReviewFixture:
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_superuser(
            username='completion-admin', phone='+963955180001',
            email='completion@example.org', password='x',
        )
        self.holder = User.objects.create_user(
            username='completion-holder', phone='+963955180002', role=User.Role.WAITER,
        )
        self.recipient = User.objects.create_user(
            username='completion-recipient', phone='+963955180003', role=User.Role.WAITER,
        )
        for user in (self.holder, self.recipient):
            UserCapabilityOverride.objects.create(user=user, capability='inventory_stock_count', allowed=True)
        self.day = open_business_day(actor=self.manager)
        set_staff_roster(self.day, actor=self.manager, user_ids=[self.holder.pk, self.recipient.pk])
        self.day.staff_assignments.update(checked_in_at=timezone.now())
        assign_duty_coverage(self.day, responsibility='operations', user_id=self.holder.pk, actor=self.manager)
        self.item = InventoryItem.objects.create(name_ar='بن يحتاج جرداً', code='RECHECK-BEAN', unit='kg')
        self.other = InventoryItem.objects.create(name_ar='سكر يحتاج جرداً', code='RECHECK-SUGAR', unit='kg')
        run_review_sync(self.day, actor=self.manager, inventory_ids=[self.item.pk, self.other.pk],
                        include_recent=False, dry_run=False)
        self.case = OperationalReviewCase.objects.get(source_id=self.item.pk, source_type='inventory')
        self.task = BusinessDayTask.objects.get(source_type='OperationalReviewCase', source_id=str(self.case.pk))
        self.url = reverse('staff_operational_tasks')

    def count(self):
        # Exercise the real no-change count flow, rather than inventing evidence.
        self.client.force_login(self.holder)
        url = reverse('staff_inventory_stock_count')
        response = self.client.post(url, {'action': 'preview', 'item': self.item.pk, 'count': '0'})
        self.assertEqual(response.status_code, 200)
        token = response.context['preview']['token']
        self.assertEqual(self.client.post(url, {'action': 'confirm', 'confirmation_token': token}).status_code, 302)

    def token(self, actor=None):
        return preview_case_recheck(self.day, self.case, actor=actor or self.holder)['token']


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class ReviewCompletionTests(ReviewFixture, TestCase):
    def test_staff_completes_one_count_with_read_only_preview_and_idempotent_confirmation(self):
        self.count()
        before = ActivityLog.objects.count()
        response = self.client.post(self.url, {'task_action': 'review_preview', 'task_id': self.task.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'تأكيد اكتمال المتابعة')
        self.assertEqual(ActivityLog.objects.count(), before)
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, 'open')
        token = response.context['recheck_preview']['token']
        for _ in range(2):
            self.assertEqual(self.client.post(self.url, {
                'task_action': 'review_confirm', 'confirmation_token': token,
            }).status_code, 302)
        self.case.refresh_from_db()
        self.task.refresh_from_db()
        self.assertEqual(self.case.status, 'resolved')
        self.assertEqual(self.task.status, 'waived')
        self.assertEqual(OperationalReviewCase.objects.filter(status='open').count(), 1)
        self.assertEqual(BusinessDayTask.objects.filter(source_type='OperationalReviewCase').count(), 2)
        self.assertEqual(ActivityLog.objects.filter(action='operational.review_rechecked').count(), 1)
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(PostingBatch.objects.exists())

    def test_unresolved_preview_explains_needed_evidence_and_does_not_offer_completion(self):
        self.client.force_login(self.holder)
        response = self.client.post(self.url, {'task_action': 'review_preview', 'task_id': self.task.pk})
        self.assertContains(response, 'المتابعة ما زالت تحتاج استكمالاً')
        self.assertContains(response, 'حتى لو طابقت الرصيد')
        self.assertNotContains(response, 'value="review_confirm"')
        result = confirm_case_recheck(self.day, actor=self.holder, token=response.context['recheck_preview']['token'])
        self.assertFalse(result['changed'])
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, 'open')

    def test_proposed_recipient_cannot_read_or_confirm_and_acceptance_revokes_previous_holder(self):
        self.count()
        token = self.token()
        request_duty_handover(self.day, responsibility='operations', to_user_id=self.recipient.pk, actor=self.holder)
        self.client.force_login(self.recipient)
        self.assertNotContains(self.client.get(self.url), self.item.name_ar)
        self.assertEqual(self.client.post(self.url, {'task_action': 'review_preview', 'task_id': self.task.pk}).status_code, 403)
        with self.assertRaises(PermissionDenied):
            confirm_case_recheck(self.day, actor=self.recipient, token=token)
        accept_duty_handover(self.day, responsibility='operations', actor=self.recipient)
        with self.assertRaises(PermissionDenied):
            confirm_case_recheck(self.day, actor=self.holder, token=token)
        self.assertTrue(confirm_case_recheck(self.day, actor=self.recipient, token=self.token(self.recipient))['changed'])

    def test_checkout_or_capability_revocation_prevents_confirmation(self):
        self.count()
        token = self.token()
        self.day.staff_assignments.filter(user=self.holder).update(checked_in_at=None)
        with self.assertRaises(PermissionDenied):
            confirm_case_recheck(self.day, actor=self.holder, token=token)
        self.day.staff_assignments.filter(user=self.holder).update(checked_in_at=timezone.now())
        UserCapabilityOverride.objects.filter(user=self.holder, capability='inventory_stock_count').update(allowed=False)
        # Requests re-load the current user and effective capabilities.
        self.client.force_login(self.holder)
        self.assertEqual(self.client.post(self.url, {'task_action': 'review_confirm', 'confirmation_token': token}).status_code, 403)

    def test_provider_and_staff_without_operations_duty_are_denied(self):
        User = get_user_model()
        provider = User.objects.create_user(username='completion-provider', phone='+963955180004', role=User.Role.INTERNET_PROVIDER)
        for actor in (provider, self.recipient):
            self.client.force_login(actor)
            self.assertEqual(self.client.post(self.url, {'task_action': 'review_preview', 'task_id': self.task.pk}).status_code, 403)

    def test_invalid_expired_wrong_actor_and_wrong_day_tokens_make_no_changes(self):
        self.count()
        token = self.token()
        for bad in ('', 'broken', token + 'x'):
            with self.assertRaises(ValidationError):
                confirm_case_recheck(self.day, actor=self.holder, token=bad)
        with patch('django.core.signing.time.time', return_value=time.time() + 601):
            with self.assertRaises(ValidationError):
                confirm_case_recheck(self.day, actor=self.holder, token=token)
        with self.assertRaises(PermissionDenied):
            confirm_case_recheck(self.day, actor=self.manager, token=token)
        tomorrow = BusinessDay.objects.create(business_date=self.day.business_date + timedelta(days=1))
        with self.assertRaises(PermissionDenied):
            confirm_case_recheck(tomorrow, actor=self.holder, token=token)
        self.assertFalse(ActivityLog.objects.filter(action='operational.review_rechecked').exists())

    def test_closed_day_and_missing_original_record_do_not_close_case(self):
        self.count()
        token = self.token()
        BusinessDay.objects.filter(pk=self.day.pk).update(status='closed')
        with self.assertRaises(ValidationError):
            confirm_case_recheck(self.day, actor=self.holder, token=token)
        BusinessDay.objects.filter(pk=self.day.pk).update(status='open')
        self.item.delete()
        with self.assertRaises(ValidationError):
            confirm_case_recheck(self.day, actor=self.holder, token=token)
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, 'open')

    def test_changed_source_requires_new_preview_and_only_existing_case_is_updated(self):
        product = Product.objects.create(category=Category.objects.create(name_ar='مشاريب'), name_ar='قهوة', price_syp=0)
        run_review_sync(self.day, actor=self.manager, product_ids=[product.pk], include_recent=False, dry_run=False)
        case = OperationalReviewCase.objects.get(source_type='product', source_id=product.pk, issue_code='price_review')
        product.price_syp = 300
        product.save(update_fields=['price_syp'])
        token = preview_case_recheck(self.day, case, actor=self.manager)['token']
        product.price_syp = 0
        product.save(update_fields=['price_syp'])
        with self.assertRaises(ValidationError):
            confirm_case_recheck(self.day, actor=self.manager, token=token)
        case.refresh_from_db()
        self.assertEqual(case.status, 'open')
        self.assertFalse(ActivityLog.objects.filter(action='operational.review_rechecked').exists())

    def test_audit_failure_rolls_back_case_and_daily_projection(self):
        self.count()
        token = self.token()
        with patch('operations.review_cases.ActivityLog.objects.create', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                confirm_case_recheck(self.day, actor=self.holder, token=token)
        self.case.refresh_from_db()
        self.task.refresh_from_db()
        self.assertEqual(self.case.status, 'open')
        self.assertEqual(self.task.status, 'pending')

    def test_receipt_link_uses_inventory_permission_and_price_opens_exact_product(self):
        UserCapabilityOverride.objects.create(user=self.holder, capability='inventory', allowed=True)
        purchase = Purchase.objects.create(business_date=timezone.localdate(), created_by=self.manager)
        case = OperationalReviewCase.objects.create(source_type='purchase', source_id=purchase.pk,
            issue_code='receipt_review', source_label='استلام يحتاج مراجعة')
        self.client.force_login(self.holder)
        actor = get_user_model().objects.get(pk=self.holder.pk)
        url = review_presentation(case, actor)['url']
        self.assertEqual(url, reverse('staff_inventory_purchase_detail', args=[purchase.pk]))
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get(reverse('staff_purchase_detail', args=[purchase.pk])).status_code, 302)
        category = Category.objects.create(name_ar='قائمة')
        chosen = Product.objects.create(category=category, name_ar='المنتج المطلوب', price_syp=0)
        Product.objects.create(category=category, name_ar='منتج آخر', price_syp=0)
        price_case = OperationalReviewCase.objects.create(source_type='product', source_id=chosen.pk,
            issue_code='price_review', source_label=chosen.name_ar)
        page = self.client.get(review_presentation(price_case, actor)['url'])
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context['products_count'], 1)
        self.assertContains(page, chosen.name_ar)
        self.assertNotContains(page, 'منتج آخر')
        self.assertEqual(self.client.get(reverse('staff_menu_tools') + '?product=invalid').context['products_count'], 0)

    def test_source_links_obey_capability_denial_and_recheck_is_csrf_protected(self):
        UserCapabilityOverride.objects.create(user=self.holder, capability='inventory_stock_count', allowed=False)
        actor = get_user_model().objects.get(pk=self.holder.pk)
        self.assertEqual(review_presentation(self.case, actor)['url'], '')
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.manager)
        self.assertEqual(client.post(self.url, {'task_action': 'review_preview', 'task_id': self.task.pk}).status_code, 403)


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class ConcurrentReviewConfirmationTests(ReviewFixture, TransactionTestCase):
    def test_concurrent_confirmations_resolve_once(self):
        self.count()
        token = self.token()

        def confirm():
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.holder.pk)
                day = BusinessDay.objects.get(pk=self.day.pk)
                return confirm_case_recheck(day, actor=actor, token=token)['changed']
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: confirm(), range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(ActivityLog.objects.filter(action='operational.review_rechecked').count(), 1)
        self.assertEqual(BusinessDayTask.objects.filter(source_type='OperationalReviewCase').count(), 2)
