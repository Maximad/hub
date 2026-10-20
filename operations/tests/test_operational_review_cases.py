"""Opt-in follow-up cannot turn legacy notebook rows into daily actions."""
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserCapabilityOverride
from core.models import (
    ActivityLog, Category, InventoryItem, Order, OrderItem, Product, ProductRecipeItem,
    StockMovement, Payment,
)
from operations.duty import (
    accept_duty_handover, assign_duty_coverage, request_duty_handover,
)
from operations.models import BusinessDay, BusinessDayTask, OperationalReviewCase
from operations.opening import set_staff_roster
from operations.review_cases import case_duty_holder, run_review_sync
from operations.services import open_business_day
from operations.tasks import set_task_status, assign_task


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class OperationalReviewCasesTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_superuser(
            username='review-admin', password='x', phone='+963991234201',
            email='review-admin@example.org',
        )
        self.tamer = User.objects.create_user(
            username='review-tamer', phone='+963991234202',
            password='x', role=User.Role.CASHIER,
        )
        self.salwa = User.objects.create_user(
            username='review-salwa', phone='+963991234203',
            password='x', role=User.Role.CASHIER,
        )
        self.waiter = User.objects.create_user(
            username='review-waiter', phone='+963991234204',
            password='x', role=User.Role.WAITER,
        )
        self.provider = User.objects.create_user(
            username='review-provider', phone='+963991234205',
            password='x', role=User.Role.INTERNET_PROVIDER,
        )
        self.day = open_business_day(actor=self.manager)
        set_staff_roster(
            self.day, actor=self.manager,
            user_ids=[self.tamer.pk, self.salwa.pk],
        )
        self.cat = Category.objects.create(name_ar='مشروبات')
        self.product = Product.objects.create(
            category=self.cat, name_ar='قهوة تحتاج مراجعة',
            price_syp=120, is_available=True, requires_preparation=True,
        )
        self.material = InventoryItem.objects.create(
            name_ar='بن جديد', code='OPS-NEW-BEAN', unit=InventoryItem.Unit.KG,
            current_quantity=Decimal('0.000'),
        )

    def sync(self, **kwargs):
        return run_review_sync(
            self.day, product_ids=[self.product.pk],
            inventory_ids=[self.material.pk],
            include_recent=False, **kwargs,
        )

    def test_preview_rolls_back_cases_tasks_and_audit(self):
        output = self.sync(dry_run=True)
        self.assertEqual(output['counts']['created'], 2)
        self.assertEqual(OperationalReviewCase.objects.count(), 0)
        self.assertEqual(
            BusinessDayTask.objects.filter(source_type='OperationalReviewCase').count(), 0,
        )
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_explicit_sync_deduplicates_and_task_cannot_be_marked_done_or_reassigned(self):
        result = self.sync(dry_run=False)
        self.assertEqual(result['counts']['created'], 2)
        self.assertEqual(result['counts']['task_created'], 2)
        self.assertEqual(OperationalReviewCase.objects.count(), 2)
        task = BusinessDayTask.objects.get(source_type='OperationalReviewCase', metadata__issue_code='needs_count')
        with self.assertRaises(ValidationError):
            set_task_status(task, actor=self.manager, status='done')
        with self.assertRaises(ValidationError):
            assign_task(task, actor=self.manager, assigned_to=self.tamer)
        again = self.sync(dry_run=False)
        self.assertEqual(again['counts']['created'], 0)
        self.assertEqual(again['counts']['task_created'], 0)
        self.assertEqual(OperationalReviewCase.objects.count(), 2)
        self.assertEqual(
            BusinessDayTask.objects.filter(source_type='OperationalReviewCase').count(), 2,
        )

    def test_count_and_corrected_recipe_close_cases_without_stock_finance_mutations(self):
        self.sync(dry_run=False)
        self.client.force_login(self.tamer)
        url = reverse('staff_inventory_stock_count')
        preview = self.client.post(url, {
            'action': 'preview', 'item': str(self.material.pk),
            'count': '0', 'reason': '',
        })
        self.assertEqual(preview.status_code, 200)
        response = self.client.post(url, {
            'action': 'confirm', 'confirmation_token': preview.context['preview']['token'],
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(StockMovement.objects.exists())
        ProductRecipeItem.objects.create(
            product=self.product, inventory_item=self.material,
            quantity_per_unit=Decimal('0.010'), unit=self.material.unit, is_active=True,
        )
        result = self.sync(dry_run=False)
        self.assertEqual(result['counts']['resolved'], 2)
        self.assertEqual(
            OperationalReviewCase.objects.filter(status='resolved').count(), 2,
        )
        self.assertFalse(
            BusinessDayTask.objects.filter(
                source_type='OperationalReviewCase', status='pending',
            ).exists()
        )
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_legacy_aggregate_sale_does_not_create_followup(self):
        order = Order.objects.create(status=Order.Status.SERVED)
        OrderItem.objects.create(
            order=order, product=self.product, quantity=88,
            product_name_ar_snapshot=self.product.name_ar,
            unit_price_syp_snapshot=120, item_note='import_key=ledger-aug-2025',
        )
        result = run_review_sync(self.day, dry_run=False)
        self.assertEqual(result['counts']['created'], 0)
        self.assertFalse(OperationalReviewCase.objects.exists())

    def test_unmarked_recent_paper_entry_cannot_trigger_automatic_review(self):
        # Manually entering a historic sale TODAY may carry no import_key.
        # A recent OrderItem timestamp alone is not operational evidence.
        order = Order.objects.create(status=Order.Status.SERVED)
        OrderItem.objects.create(
            order=order, product=self.product, quantity=88,
            product_name_ar_snapshot=self.product.name_ar,
            unit_price_syp_snapshot=120, item_note='دفتر ورقي دون وسم استيراد',
        )
        output = run_review_sync(self.day, dry_run=False)
        self.assertEqual(output['counts']['created'], 0)
        self.assertFalse(OperationalReviewCase.objects.exists())

    def test_recent_staff_pos_order_creates_one_recipe_review_only(self):
        order = Order.objects.create(status=Order.Status.SERVED)
        OrderItem.objects.create(
            order=order, product=self.product, quantity=1,
            product_name_ar_snapshot=self.product.name_ar,
            unit_price_syp_snapshot=120, item_note='',
        )
        ActivityLog.objects.create(
            actor=self.manager,
            action='staff_pos_order_created',
            details={'order_public_code': str(order.public_code), 'source': 'staff_pos'},
        )
        output = run_review_sync(self.day, dry_run=False)
        self.assertEqual(output['counts']['created'], 1)
        self.assertTrue(OperationalReviewCase.objects.filter(
            source_type='product', source_id=self.product.pk,
            issue_code='recipe_review',
        ).exists())

    def test_import_marked_row_never_triggers_review_even_with_pos_receipt(self):
        order = Order.objects.create(status=Order.Status.SERVED)
        OrderItem.objects.create(
            order=order, product=self.product, quantity=80,
            product_name_ar_snapshot=self.product.name_ar,
            unit_price_syp_snapshot=120, item_note='import_key=legacy-2025',
        )
        ActivityLog.objects.create(
            actor=self.manager, action='staff_pos_order_created',
            details={'order_public_code': str(order.public_code)},
        )
        output = run_review_sync(self.day, dry_run=False)
        self.assertEqual(output['counts']['created'], 0)

    def test_unrelated_pos_receipt_cannot_prove_imported_order(self):
        order = Order.objects.create(status=Order.Status.SERVED)
        other = Order.objects.create(status=Order.Status.SERVED)
        OrderItem.objects.create(
            order=order, product=self.product, quantity=18,
            product_name_ar_snapshot=self.product.name_ar,
            unit_price_syp_snapshot=120, item_note='',
        )
        ActivityLog.objects.create(
            actor=self.manager, action='staff_pos_order_created',
            details={'order_public_code': str(other.public_code)},
        )
        output = run_review_sync(self.day, dry_run=False)
        self.assertEqual(output['counts']['created'], 0)

    def test_explicit_operations_duty_holder_and_acceptance_change_visibility(self):
        # Cashier is a manager in this app and legitimately sees uncovered
        # cases. Test the stricter on-duty visibility with non-manager staff
        # who are individually granted the narrowly scoped count capability.
        User = get_user_model()
        for member in (self.tamer, self.salwa):
            member.role = User.Role.WAITER
            member.save(update_fields=['role'])
            UserCapabilityOverride.objects.create(
                user=member, capability='inventory_stock_count', allowed=True,
            )
        self.sync(dry_run=False)
        case = OperationalReviewCase.objects.get(issue_code='needs_count')
        self.assertIsNone(case_duty_holder(self.day, case))
        self.client.force_login(self.tamer)
        self.assertNotContains(self.client.get(reverse('staff_operational_tasks')), case.source_label)
        self.day.staff_assignments.update(checked_in_at=timezone.now())
        assign_duty_coverage(
            self.day, responsibility='operations', user_id=self.tamer.pk,
            actor=self.manager,
        )
        self.assertEqual(case_duty_holder(self.day, case), self.tamer)
        self.assertContains(self.client.get(reverse('staff_operational_tasks')), case.source_label)
        request_duty_handover(
            self.day, responsibility='operations', to_user_id=self.salwa.pk,
            actor=self.tamer,
        )
        self.assertEqual(case_duty_holder(self.day, case), self.tamer)
        accept_duty_handover(self.day, responsibility='operations', actor=self.salwa)
        self.assertEqual(case_duty_holder(self.day, case), self.salwa)
        self.assertNotContains(self.client.get(reverse('staff_operational_tasks')), case.source_label)
        self.client.force_login(self.salwa)
        self.assertContains(self.client.get(reverse('staff_operational_tasks')), case.source_label)

    def test_review_sync_action_is_manager_only(self):
        self.client.force_login(self.waiter)
        self.client.post(reverse('staff_operational_tasks'), {'task_action': 'review_sync'})
        self.assertFalse(OperationalReviewCase.objects.exists())
        self.client.force_login(self.manager)
        response = self.client.post(reverse('staff_operational_tasks'), {'task_action': 'review_sync'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(OperationalReviewCase.objects.exists())

    def test_command_requires_actor_for_apply_and_enforces_limits(self):
        with self.assertRaises(CommandError):
            call_command('review_operational_followups', '--apply')
        with self.assertRaises(CommandError):
            call_command('review_operational_followups', '--limit', '0')
        preview = StringIO()
        call_command('review_operational_followups', '--product-id', str(self.product.pk),
                     '--no-recent', stdout=preview)
        self.assertIn('PREVIEW ONLY', preview.getvalue())
        self.assertFalse(OperationalReviewCase.objects.exists())
        commit = StringIO()
        call_command('review_operational_followups', '--product-id', str(self.product.pk),
                     '--no-recent', '--apply', '--actor', self.manager.username,
                     stdout=commit)
        self.assertIn('APPLIED', commit.getvalue())
        self.assertTrue(OperationalReviewCase.objects.exists())

    def test_new_day_projection_is_bounded_and_historic_tasks_unchanged(self):
        self.sync(dry_run=False)
        previous_count = BusinessDayTask.objects.filter(source_type='OperationalReviewCase').count()
        self.day.status = BusinessDay.Status.CLOSED
        self.day.save(update_fields=['status'])
        next_day = open_business_day(
            actor=self.manager, business_date=self.day.business_date + timedelta(days=1)
        )
        run_review_sync(next_day, include_recent=False, dry_run=False)
        self.assertEqual(
            BusinessDayTask.objects.filter(source_type='OperationalReviewCase').count(),
            previous_count + 2,
        )
        self.assertEqual(
            BusinessDayTask.objects.filter(
                business_day=self.day, source_type='OperationalReviewCase', status='pending',
            ).count(), 2,
        )
