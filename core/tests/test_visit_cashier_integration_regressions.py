from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import (
    Category,
    HubVisit,
    Order,
    OrderDiscount,
    OrderItem,
    Product,
    Room,
    SystemSetting,
    TableArea,
)
from core.settings_helpers import get_system_settings


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class VisitCashierIntegrationRegressionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='visit-account-regression-admin',
            password='adminpass',
            phone='+96319201',
            role='admin',
        )
        self.room = Room.objects.create(name_ar='مشاريب')
        self.table = TableArea.objects.create(room=self.room, name_ar='طاولة التجميع')
        self.category = Category.objects.create(name_ar='اختبار التجميع')
        self.product = Product.objects.create(
            category=self.category,
            name_ar='صنف تجريبي',
            price_syp=100,
        )
        SystemSetting.objects.create()
        get_system_settings.cache_clear()
        self.client.force_login(self.admin)

    def tearDown(self):
        get_system_settings.cache_clear()

    def _order(self, amount, *, visit=None):
        order = Order.objects.create(
            table=self.table if visit else None,
            visit=visit,
            service_mode=Order.ServiceMode.TABLE if visit else Order.ServiceMode.DINE_IN,
            fulfillment_mode=(
                Order.FulfillmentMode.TABLE
                if visit else Order.FulfillmentMode.INSIDE_SPACE
            ),
            status=Order.Status.READY,
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            quantity=1,
            product_name_ar_snapshot=self.product.name_ar,
            unit_price_syp_snapshot=amount,
            line_total_syp_snapshot=amount,
        )
        return order

    def test_workspace_counts_one_unpaid_visit_as_one_account(self):
        visit = HubVisit.objects.create(table=self.table, created_by=self.admin)
        self._order(100, visit=visit)
        self._order(200, visit=visit)
        self._order(50)

        response = self.client.get(reverse('staff_home'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['workspace_stats']['unpaid_orders'], 2)
        self.assertContains(response, 'حسابات بحاجة إلى تسديد')

    def test_order_discount_remains_available_and_updates_combined_visit_total(self):
        visit = HubVisit.objects.create(table=self.table, created_by=self.admin)
        first = self._order(200, visit=visit)
        second = self._order(300, visit=visit)

        account_url = reverse(
            'staff_cashier_order', kwargs={'public_code': visit.public_code}
        )
        before = self.client.get(account_url)
        self.assertEqual(before.context['total'], 500)
        self.assertContains(before, 'خصم على هذا الطلب')

        response = self.client.post(
            reverse('staff_cashier_discount', kwargs={'public_code': first.public_code}),
            {
                'discount_type': OrderDiscount.DiscountType.FIXED,
                'amount_syp': '50',
                'reason': 'اختبار خصم الحساب المجمع',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            OrderDiscount.objects.filter(
                order=first,
                is_active=True,
                amount_syp=50,
            ).exists()
        )
        first.refresh_from_db(); second.refresh_from_db(); visit.refresh_from_db()
        self.assertEqual(first.total_syp, 150)
        self.assertEqual(second.total_syp, 300)
        self.assertEqual(visit.gross_syp, 450)

        after = self.client.get(account_url)
        self.assertEqual(after.context['total'], 450)
        self.assertContains(after, 'اختبار خصم الحساب المجمع')
