from datetime import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Category, Order, OrderItem, Product
from core.views_legacy import DAMASCUS_TZ


class FinancialPerformanceDashboardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='performance-admin',
            password='pass',
            phone='+963000000099',
            role='admin',
            is_staff=False,
        )
        self.client.force_login(self.user)
        category = Category.objects.create(name_ar='مشروبات')
        product = Product.objects.create(
            category=category,
            name_ar='قهوة تاريخية',
            price_syp=100,
            estimated_unit_cost_syp=40,
        )
        order = Order.objects.create(
            status=Order.Status.SERVED,
            notes='[PRE_SYSTEM_HISTORICAL_SALES] month=2026-08',
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            quantity=2,
            product_name_ar_snapshot=product.name_ar,
            unit_price_syp_snapshot=100,
            line_total_syp_snapshot=200,
            estimated_unit_cost_syp_snapshot=40,
            estimated_line_cost_syp_snapshot=80,
            estimated_line_margin_syp_snapshot=120,
            item_note='import_key=2026-08:coffee; review=needs_review',
            stock_deducted=False,
        )
        historical_time = timezone.make_aware(
            datetime(2026, 8, 31, 12, 0), DAMASCUS_TZ
        )
        Order.objects.filter(pk=order.pk).update(created_at=historical_time)
        OrderItem.objects.filter(order=order).update(created_at=historical_time)

    @override_settings(DEBUG_PROPAGATE_EXCEPTIONS=True, ALLOWED_HOSTS=['testserver'])
    def test_reports_home_is_performance_dashboard_and_flags_historical_quality(self):
        response = self.client.get(
            '/staff/reports/?date_from=2026-08-01&date_to=2026-08-31'
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'الأداء المالي والتشغيلي')
        self.assertContains(response, 'قهوة تاريخية')
        self.assertContains(response, '2026-08')
        self.assertContains(response, 'تاريخي مجمّع')
        self.assertContains(response, 'صفاً تاريخياً معلماً للمراجعة')
        self.assertEqual(response.context['totals']['units_sold'], 2)
        self.assertEqual(response.context['totals']['gross_sales_syp'], 200)
        self.assertEqual(response.context['historical_needs_review'], 1)
        self.assertIsNone(response.context['totals']['recorded_operating_result_syp'])
