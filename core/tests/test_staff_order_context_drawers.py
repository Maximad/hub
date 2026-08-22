from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.http import HttpResponse, HttpResponseRedirect
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import HubVisit, Order, Room, SystemSetting, TableArea
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
        self.order = Order.objects.create(
            table=self.table,
            visit=self.visit,
            status=Order.Status.READY,
            fulfillment_mode=Order.FulfillmentMode.TABLE,
            service_mode=Order.ServiceMode.TABLE,
        )
        SystemSetting.objects.create()
        get_system_settings.cache_clear()

    def tearDown(self):
        get_system_settings.cache_clear()

    def test_workspace_wires_order_and_payment_context(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("staff_home"))

        order_url = reverse("staff_order_edit", kwargs={"public_code": self.order.public_code})
        cashier_url = reverse("staff_cashier_order", kwargs={"public_code": self.order.public_code})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'data-staff-context-url="{order_url}?panel=context"')
        self.assertContains(response, f'data-staff-context-url="{cashier_url}?panel=payment"')
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
        self.assertNotContains(response, "قبض الطلب")
        self.assertNotContains(response, "تفاصيل الكاشير")

    def test_cashier_gets_compact_payment_panel(self):
        self.client.force_login(self.cashier)
        payment_url = reverse("staff_cashier_order", kwargs={"public_code": self.order.public_code})
        pay_url = reverse("staff_cashier_pay", kwargs={"public_code": self.order.public_code})
        response = self.client.get(payment_url, {"panel": "payment"})

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "staff/_payment_panel.html")
        self.assertContains(response, "قبض الطلب")
        self.assertContains(response, "data-payment-panel")
        self.assertContains(response, f'hx-post="{pay_url}?panel=payment"')
        self.assertContains(response, "data-currency-entry")

    def test_contextual_payment_post_delegates_to_canonical_cashier_handler(self):
        self.client.force_login(self.cashier)
        pay_url = reverse("staff_cashier_pay", kwargs={"public_code": self.order.public_code})

        with patch(
            "core.views.menu._legacy_staff_cashier_pay",
            return_value=HttpResponseRedirect("/staff/cashier/"),
        ) as legacy_pay, patch(
            "core.views.menu.render_payment_panel",
            return_value=HttpResponse("payment-panel"),
        ) as render_panel:
            response = self.client.post(f"{pay_url}?panel=payment", {"currency_amount": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"payment-panel")
        legacy_pay.assert_called_once()
        render_panel.assert_called_once_with(response.wsgi_request, self.order.public_code)

    def test_regular_cashier_url_keeps_full_page_fallback(self):
        self.client.force_login(self.cashier)
        response = self.client.get(
            reverse("staff_cashier_order", kwargs={"public_code": self.order.public_code})
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "staff/cashier_order.html")
        self.assertTemplateNotUsed(response, "staff/_payment_panel.html")

    def test_kitchen_workspace_does_not_expose_order_payment_drawer(self):
        self.client.force_login(self.kitchen)
        response = self.client.get(reverse("staff_home"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "data-staff-context-url")
        self.assertNotContains(response, 'id="staff-context-drawer"')
