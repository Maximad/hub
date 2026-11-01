"""The cashier defaults to actionable balances; old and empty records remain inspectable."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Category, HubVisit, Order, OrderItem, Payment, Product, Room, TableArea


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class CashierActionableQueueTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username='actionable-cashier-admin', phone='+963998100099', role='admin',
        )
        self.client.force_login(user)
        room = Room.objects.create(name_ar='منطقة الحسابات')
        self.table = TableArea.objects.create(room=room, name_ar='طاولة المتابعة')
        category = Category.objects.create(name_ar='اختبار الطابور')
        self.product = Product.objects.create(category=category, name_ar='طلب الدفع', price_syp=100)

    def _order(self, *, visit=None, amount=100, paid=False):
        order = Order.objects.create(table=self.table if visit else None, visit=visit, status=Order.Status.NEW)
        OrderItem.objects.create(
            order=order, product=self.product, quantity=1,
            product_name_ar_snapshot='اختبار الدفع', unit_price_syp_snapshot=amount,
            line_total_syp_snapshot=amount,
        )
        if paid:
            Payment.objects.create(order=order, amount_syp=amount, method=Payment.Method.CASH)
        return order

    def test_actionable_default_excludes_settled_and_empty_and_history_retains_them(self):
        unpaid_visit = HubVisit.objects.create(table=self.table)
        unpaid = self._order(visit=unpaid_visit)
        paid_visit = HubVisit.objects.create(table=self.table)
        settled = self._order(visit=paid_visit, paid=True)
        empty_visit = HubVisit.objects.create(table=self.table)
        unpaid_standalone = self._order()
        settled_standalone = self._order(paid=True)

        url = reverse('staff_cashier')
        default = self.client.get(url)
        self.assertEqual(default.status_code, 200)
        self.assertEqual(default.context['view_mode'], 'collect')
        self.assertEqual([r['visit'].pk for r in default.context['visit_rows']], [unpaid_visit.pk])
        self.assertEqual([r['order'].pk for r in default.context['standalone_rows']], [unpaid_standalone.pk])
        self.assertEqual(default.context['collect_count'], 2)
        self.assertEqual(default.context['review_count'], 3)
        self.assertContains(default, 'بحاجة لمتابعة أو تحصيل')
        self.assertContains(default, f'href="{reverse("staff_cashier_order", args=[unpaid_visit.public_code])}"')

        review = self.client.get(url, {'view': 'review'})
        self.assertEqual(review.status_code, 200)
        self.assertCountEqual([r['visit'].pk for r in review.context['visit_rows']],
                              [paid_visit.pk, empty_visit.pk])
        self.assertEqual([r['order'].pk for r in review.context['standalone_rows']],
                         [settled_standalone.pk])
        self.assertContains(review, 'عرض الحساب')
        self.assertContains(review, 'عرض الطلب')
        self.assertEqual(Payment.objects.filter(order__in=[settled, settled_standalone]).count(), 2)
        self.assertEqual(OrderItem.objects.filter(order=unpaid).count(), 1)

    def test_recent_empty_visits_do_not_displace_a_payable_bill(self):
        visit = HubVisit.objects.create(table=self.table)
        self._order(visit=visit, amount=150)
        for _ in range(15):
            HubVisit.objects.create(table=self.table)
        response = self.client.get(reverse('staff_cashier'))
        self.assertEqual([row['visit'].pk for row in response.context['visit_rows']], [visit.pk])
        self.assertEqual(response.context['review_count'], 15)
