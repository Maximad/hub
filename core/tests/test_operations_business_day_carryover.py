"""Business-day rollover is a read-only view transition, not a settlement."""
from datetime import date, datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import ActivityLog, Category, HubVisit, Order, OrderItem, Payment, Product
from core.services.staff_attention import get_staff_attention
from operations.models import BusinessDay, BusinessDayTask
from operations.services import current_business_date


@override_settings(
    BUSINESS_DAY_CUTOFF_HOUR=4,
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class BusinessDayCarryoverWorkspaceTests(TestCase):
    def setUp(self):
        self.cashier = get_user_model().objects.create_user(
            username='carryover-cashier', phone='098600101', role='cashier',
        )
        self.client.force_login(self.cashier)
        category = Category.objects.create(name_ar='اختبار الترحيل')
        self.product = Product.objects.create(
            category=category, name_ar='مشروب مرحل', price_syp=250,
        )
        self.day_date = current_business_date()
        self.start = timezone.make_aware(
            datetime.combine(self.day_date, time(hour=4)),
            timezone.get_current_timezone(),
        )
        self.old_stamp = self.start - timedelta(hours=3)
        self.now_stamp = self.start + timedelta(minutes=5)

    def order(self, *, status, old=True, visit=None, amount=250, paid=0):
        order = Order.objects.create(visit=visit, status=status)
        OrderItem.objects.create(
            order=order, product=self.product, quantity=1,
            product_name_ar_snapshot=self.product.name_ar,
            unit_price_syp_snapshot=amount, line_total_syp_snapshot=amount,
            prep_status=OrderItem.PrepStatus.NO_PREP,
        )
        if paid:
            Payment.objects.create(order=order, method=Payment.Method.CASH, amount_syp=paid)
        if old:
            Order.objects.filter(pk=order.pk).update(created_at=self.old_stamp)
        order.refresh_from_db()
        return order

    def test_paid_prior_day_ready_handoff_stays_visible_even_if_visit_is_stale(self):
        visit = HubVisit.objects.create()
        HubVisit.objects.filter(pk=visit.pk).update(last_activity_at=self.old_stamp)
        ready = self.order(status=Order.Status.READY, visit=visit, paid=250)
        response = self.client.get(reverse('staff_home'))
        ids = [o.pk for o in response.context['active_orders']]
        self.assertIn(ready.pk, ids)
        self.assertEqual(response.context['workspace_stats']['ready_orders'], 1)
        self.assertEqual(response.context['workspace_stats']['carryover_orders'], 1)
        shown = next(o for o in response.context['active_orders'] if o.pk == ready.pk)
        self.assertTrue(shown.workspace_carryover)
        self.assertContains(response, 'من يوم عمل سابق')
        self.assertEqual(response.context['workspace_stats']['unpaid_orders'], 0)

    def test_prior_day_standalone_ready_order_keeps_kitchen_handoff(self):
        ready = self.order(status=Order.Status.READY)
        response = self.client.get(reverse('staff_home'))
        self.assertIn(ready.pk, [o.pk for o in response.context['active_orders']])
        self.assertContains(response, 'جاهز للتسليم')
        self.assertEqual(response.context['workspace_stats']['carryover_orders'], 1)

    def test_prior_day_served_unpaid_standalone_is_in_separate_collection_review(self):
        older_due = self.order(status=Order.Status.SERVED, paid=0)
        current_due = self.order(status=Order.Status.SERVED, old=False, paid=0)
        older_paid = self.order(status=Order.Status.SERVED, paid=250)
        response = self.client.get(reverse('staff_home'))
        self.assertIn(older_due.pk, [o.pk for o in response.context['historical_to_collect']])
        self.assertNotIn(older_paid.pk, [o.pk for o in response.context['historical_to_collect']])
        self.assertIn(current_due.pk, [o.pk for o in response.context['standalone_to_collect']])
        self.assertNotIn(older_due.pk, [o.pk for o in response.context['standalone_to_collect']])
        self.assertEqual(response.context['workspace_stats']['historical_to_collect'], 1)
        self.assertEqual(response.context['workspace_stats']['unpaid_orders'], 1)
        self.assertContains(response, 'طلبات منفردة من أيام سابقة تنتظر القبض')
        self.assertContains(response, reverse('staff_cashier_order', args=[older_due.public_code]))

    def test_stale_visit_debt_stays_in_existing_old_accounts_section(self):
        visit = HubVisit.objects.create()
        HubVisit.objects.filter(pk=visit.pk).update(last_activity_at=self.old_stamp)
        debt = self.order(status=Order.Status.SERVED, visit=visit)
        response = self.client.get(reverse('staff_home'))
        self.assertIn(visit.pk, [r['visit'].pk for r in response.context['stale_visit_rows']])
        self.assertNotIn(visit.pk, [r['visit'].pk for r in response.context['visit_rows']])
        self.assertNotIn(debt.pk, [o.pk for o in response.context['active_orders']])
        self.assertEqual(debt.remaining_syp, 250)

    def test_closed_visit_and_completed_order_do_not_resurface(self):
        visit = HubVisit.objects.create()
        ready = self.order(status=Order.Status.READY, visit=visit)
        visit.status = HubVisit.Status.CLOSED
        visit.save(update_fields=['status', 'updated_at'])
        done = self.order(status=Order.Status.SERVED, paid=250)
        response = self.client.get(reverse('staff_home'))
        self.assertNotIn(ready.pk, [o.pk for o in response.context['active_orders']])
        self.assertNotIn(done.pk, [o.pk for o in response.context['active_orders']])
        self.assertEqual(response.context['workspace_stats']['carryover_orders'], 0)

    def test_old_open_business_day_stays_historical_without_auto_close_or_task_leak(self):
        previous = BusinessDay.objects.create(
            business_date=self.day_date - timedelta(days=1),
            status=BusinessDay.Status.OPEN,
        )
        old_task = BusinessDayTask.objects.create(
            business_day=previous, fingerprint='old-shift-close',
            title_ar='مراجعة صندوق الأمس', priority='high',
            status=BusinessDayTask.Status.PENDING,
        )
        before_log_count = ActivityLog.objects.count()
        response = self.client.get(reverse('staff_home'))
        self.assertEqual(response.context['staff_attention']['tasks_count'], 0)
        self.assertEqual(response.context['previous_open_business_day'].pk, previous.pk)
        self.assertContains(response, 'يوم تشغيلي سابق مفتوح')
        self.assertContains(response, 'مراجعة الإغلاق')
        previous.refresh_from_db()
        old_task.refresh_from_db()
        self.assertEqual(previous.status, BusinessDay.Status.OPEN)
        self.assertEqual(old_task.status, BusinessDayTask.Status.PENDING)
        self.assertEqual(ActivityLog.objects.count(), before_log_count)

        previous.status = BusinessDay.Status.CLOSED
        previous.save(update_fields=['status'])
        current = BusinessDay.objects.create(
            business_date=self.day_date,
            status=BusinessDay.Status.OPEN,
        )
        current_task = BusinessDayTask.objects.create(
            business_day=current, fingerprint='new-shift',
            title_ar='فتح الوردية الحالية', priority='high',
        )
        refreshed = self.client.get(reverse('staff_home'))
        self.assertIsNone(refreshed.context['previous_open_business_day'])
        self.assertEqual(refreshed.context['staff_attention']['tasks_count'], 1)
        self.assertEqual(refreshed.context['staff_attention']['tasks'][0].pk, current_task.pk)
        old_task.refresh_from_db()
        self.assertEqual(old_task.status, BusinessDayTask.Status.PENDING)

    def test_0400_boundary_reselects_current_task_day_without_writes(self):
        yesterday = date(2026, 10, 24)
        today = yesterday + timedelta(days=1)
        old_day = BusinessDay.objects.create(
            business_date=yesterday, status=BusinessDay.Status.OPEN,
        )
        task = BusinessDayTask.objects.create(
            business_day=old_day, fingerprint='day-rollover',
            title_ar='مراجعة سابقة', priority='high',
        )
        tz = timezone.get_current_timezone()
        at_0359 = timezone.make_aware(datetime(2026, 10, 25, 3, 59), tz)
        at_0400 = timezone.make_aware(datetime(2026, 10, 25, 4, 0), tz)
        prior = get_staff_attention(self.cashier, {'members/internet': False}, now=at_0359)
        after = get_staff_attention(self.cashier, {'members/internet': False}, now=at_0400)
        self.assertEqual(prior['tasks_count'], 1)
        self.assertEqual(prior['tasks'][0].pk, task.pk)
        self.assertEqual(after['tasks_count'], 0)
        task.refresh_from_db()
        self.assertEqual(task.status, BusinessDayTask.Status.PENDING)

    def test_operations_get_is_nonmutating_across_rollover(self):
        ready = self.order(status=Order.Status.READY)
        older_debt = self.order(status=Order.Status.SERVED)
        counts = (Order.objects.count(), Payment.objects.count(),
                  ActivityLog.objects.count())
        for _ in range(2):
            r = self.client.get(reverse('staff_home'))
            self.assertIn(ready.pk, [o.pk for o in r.context['active_orders']])
            self.assertIn(older_debt.pk, [o.pk for o in r.context['historical_to_collect']])
        self.assertEqual(counts, (Order.objects.count(), Payment.objects.count(),
                                  ActivityLog.objects.count()))
