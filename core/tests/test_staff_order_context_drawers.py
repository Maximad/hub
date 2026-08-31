from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import (
    Category,
    HubVisit,
    Order,
    OrderItem,
    Product,
    Room,
    SystemSetting,
    TableArea,
)
from core.settings_helpers import get_system_settings


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    SECURE_SSL_REDIRECT=False,
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
)
class StaffOrderContextDrawerTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username="context-admin", phone="93001", password="x", role="admin"
        )
        self.cashier = User.objects.create_user(
            username="context-cashier", phone="93002", password="x", role="cashier"
        )
        self.waiter = User.objects.create_user(
            username="context-waiter", phone="93003", password="x", role="waiter"
        )
        self.kitchen = User.objects.create_user(
            username="context-kitchen", phone="93004", password="x", role="kitchen"
        )
        self.room = Room.objects.create(name_ar="الصالة")
        self.table = TableArea.objects.create(room=self.room, name_ar="طاولة 8")
        self.visit = HubVisit.objects.create(table=self.table)
        self.category = Category.objects.create(name_ar="اختبار")
        self.product = Product.objects.create(
            category=self.category,
            name_ar="منتج اختبار",
            price_syp=400,
        )
        self.order = Order.objects.create(
            table=self.table,
            visit=self.visit,
            status=Order.Status.READY,
            fulfillment_mode=Order.FulfillmentMode.TABLE,
            service_mode=Order.ServiceMode.TABLE,
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            quantity=1,
            product_name_ar_snapshot=self.product.name_ar,
            unit_price_syp_snapshot=400,
            line_total_syp_snapshot=400,
        )
        self.standalone_order = Order.objects.create(status=Order.Status.READY)
        OrderItem.objects.create(
            order=self.standalone_order,
            product=self.product,
            quantity=1,
            product_name_ar_snapshot=self.product.name_ar,
            unit_price_syp_snapshot=400,
            line_total_syp_snapshot=400,
        )
        SystemSetting.objects.create()
        get_system_settings.cache_clear()

    def tearDown(self):
        get_system_settings.cache_clear()

    def test_workspace_routes_visit_orders_to_account_and_payment_context(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("staff_home"))

        visit_url = reverse(
            "staff_visit_detail", kwargs={"public_code": self.visit.public_code}
        )
        visit_cashier_url = reverse(
            "staff_cashier_order", kwargs={"public_code": self.visit.public_code}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'data-visit-context-url="{visit_url}?panel=1"')
        self.assertContains(
            response,
            f'data-staff-context-url="{visit_cashier_url}?panel=payment"',
        )
        self.assertContains(response, "الدفع وإغلاق الحساب")
        self.assertContains(response, "js/currency-entry.js")
        self.assertContains(response, "js/staff_workspace.js")

    def test_waiter_can_inspect_order_without_payment_action(self):
        self.client.force_login(self.waiter)
        response = self.client.get(
            reverse("staff_order_edit", kwargs={"public_code": self.order.public_code}),
            {"panel": "context"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "staff/_order_panel.html")
        self.assertContains(response, self.order.display_number)
        self.assertContains(response, "تعديل الطلب")
        self.assertNotContains(response, "دفع الحساب")
        self.assertNotContains(response, "تفاصيل الكاشير")

    def test_visit_order_cashier_link_gets_combined_payment_panel(self):
        self.client.force_login(self.cashier)
        payment_url = reverse(
            "staff_cashier_order", kwargs={"public_code": self.order.public_code}
        )
        visit_pay_url = reverse(
            "staff_cashier_pay", kwargs={"public_code": self.visit.public_code}
        )
        response = self.client.get(payment_url, {"panel": "payment"})

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "staff/_visit_payment_panel.html")
        self.assertContains(response, "الدفع وإغلاق الحساب")
        self.assertContains(response, "data-payment-panel")
        self.assertContains(response, f'hx-post="{visit_pay_url}?panel=payment"')
        self.assertContains(response, "data-currency-entry")
        self.assertContains(response, self.order.display_number)

    def test_contextual_visit_payment_post_delegates_to_visit_handler(self):
        self.client.force_login(self.cashier)
        pay_url = reverse(
            "staff_cashier_pay", kwargs={"public_code": self.order.public_code}
        )

        with patch(
            "core.views.menu.staff_cashier_visit_pay",
            return_value=HttpResponse("visit-payment-panel"),
        ) as visit_pay:
            response = self.client.post(
                f"{pay_url}?panel=payment", {"currency_amount": "100"}
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"visit-payment-panel")
        visit_pay.assert_called_once()
        self.assertEqual(visit_pay.call_args.args[1], self.visit.public_code)

    def test_regular_cashier_url_for_visit_order_opens_combined_account(self):
        self.client.force_login(self.cashier)
        response = self.client.get(
            reverse("staff_cashier_order", kwargs={"public_code": self.order.public_code})
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "staff/cashier_visit.html")
        self.assertTemplateNotUsed(response, "staff/cashier_order.html")
        self.assertContains(response, "الحساب المجمع")
        self.assertContains(response, self.order.display_number)

    def test_standalone_order_keeps_legacy_payment_panel_and_full_page(self):
        self.client.force_login(self.cashier)
        payment_url = reverse(
            "staff_cashier_order", kwargs={"public_code": self.standalone_order.public_code}
        )
        panel = self.client.get(payment_url, {"panel": "payment"})
        full = self.client.get(payment_url)

        self.assertEqual(panel.status_code, 200)
        self.assertTemplateUsed(panel, "staff/_payment_panel.html")
        self.assertContains(panel, "قبض الطلب")
        self.assertEqual(full.status_code, 200)
        self.assertTemplateUsed(full, "staff/cashier_order.html")

    def test_kitchen_workspace_does_not_expose_order_payment_drawer(self):
        self.client.force_login(self.kitchen)
        response = self.client.get(reverse("staff_home"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "data-staff-context-url")
        self.assertNotContains(response, 'id="staff-context-drawer"')
