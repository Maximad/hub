from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Category, Order, OrderItem, Payment, Product


class ProductAdminStabilizationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='admin-audit',
            email='admin-audit@example.com',
            password='pass',
            phone='+963000000001',
        )
        self.client.force_login(self.user)
        self.category = Category.objects.create(name_ar='مشروبات')
        self.product = Product.objects.create(
            category=self.category,
            name_ar='قهوة اختبار',
            name_en='',
            price_syp=1000,
            estimated_unit_cost_syp=None,
        )

    @override_settings(DEBUG_PROPAGATE_EXCEPTIONS=True, ALLOWED_HOSTS=['testserver'])
    def test_product_admin_list_loads_with_missing_cost_media_and_recipe(self):
        response = self.client.get(reverse('admin:core_product_changelist'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'قهوة اختبار')
        self.assertContains(response, 'غير محدد')

    @override_settings(DEBUG_PROPAGATE_EXCEPTIONS=True, ALLOWED_HOSTS=['testserver'])
    def test_product_admin_detail_loads_with_missing_cost_media_and_recipe(self):
        response = self.client.get(reverse('admin:core_product_change', args=[self.product.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'قهوة اختبار')


class PaymentStabilizationTests(TestCase):
    def test_payment_clean_rejects_overpayment(self):
        category = Category.objects.create(name_ar='مشروبات')
        product = Product.objects.create(category=category, name_ar='شاي', price_syp=1000)
        order = Order.objects.create()
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name_ar_snapshot=product.name_ar,
            unit_price_syp_snapshot=product.price_syp,
            quantity=1,
            line_total_syp_snapshot=product.price_syp,
        )
        Payment.objects.create(order=order, amount_syp=800, method=Payment.Method.CASH)

        payment = Payment(order=order, amount_syp=300, method=Payment.Method.CASH)
        with self.assertRaises(ValidationError):
            payment.full_clean()

class ProductMarginReportStabilizationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='report-admin',
            password='pass',
            phone='+963000000003',
            role='admin',
            is_staff=False,
        )
        self.client.force_login(self.user)
        category = Category.objects.create(name_ar='مشروبات')
        Product.objects.create(category=category, name_ar='منتج بلا كلفة', price_syp=1500)

    @override_settings(DEBUG_PROPAGATE_EXCEPTIONS=True, ALLOWED_HOSTS=['testserver'])
    def test_product_margin_report_loads_with_missing_cost(self):
        response = self.client.get('/staff/reports/products/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'منتج بلا كلفة')
