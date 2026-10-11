from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import (
    ActivityLog,
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
        self.item = OrderItem.objects.create(
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

    def test_workspace_edit_link_opens_quick_order_drawer(self):
        self.client.force_login(self.waiter)
        response = self.client.get(reverse("staff_home"))

        edit_url = reverse(
            "staff_order_edit", kwargs={"public_code": self.order.public_code}
        )
        self.assertContains(
            response,
            f'data-staff-context-url="{edit_url}?panel=context"',
        )
        self.assertContains(response, 'data-context-title="تعديل سريع للطلب"')

    def test_order_drawer_contains_quick_item_and_cancel_mutations(self):
        self.client.force_login(self.waiter)
        response = self.client.get(
            reverse("staff_order_edit", kwargs={"public_code": self.order.public_code}),
            {"panel": "context"},
        )

        update_url = reverse(
            "staff_order_edit_update_item",
            kwargs={"public_code": self.order.public_code, "item_id": self.item.pk},
        )
        remove_url = reverse(
            "staff_order_edit_remove_item",
            kwargs={"public_code": self.order.public_code, "item_id": self.item.pk},
        )
        self.assertContains(response, update_url)
        self.assertContains(response, remove_url)
        self.assertContains(response, "إلغاء الطلب")
        self.assertContains(
            response,
            f'<input type="hidden" name="next" value="{reverse("staff_home")}">',
            html=False,
        )

    def test_quick_quantity_update_returns_to_workspace(self):
        self.client.force_login(self.waiter)
        response = self.client.post(
            reverse(
                "staff_order_edit_update_item",
                kwargs={"public_code": self.order.public_code, "item_id": self.item.pk},
            ),
            {
                "quantity": "2",
                "item_note": "",
                "edit_scope": "basic",
                "next": reverse("staff_home"),
            },
        )

        self.assertRedirects(response, reverse("staff_home"), fetch_redirect_response=False)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 2)

    def test_quick_edit_rejects_external_return_target(self):
        self.client.force_login(self.waiter)
        response = self.client.post(
            reverse(
                "staff_order_edit_update_item",
                kwargs={"public_code": self.order.public_code, "item_id": self.item.pk},
            ),
            {
                "quantity": "2",
                "item_note": "",
                "edit_scope": "basic",
                "next": "https://example.invalid/leave-hub",
            },
        )

        self.assertRedirects(
            response,
            reverse("staff_order_edit", kwargs={"public_code": self.order.public_code}),
            fetch_redirect_response=False,
        )

    def test_quick_remove_returns_to_workspace(self):
        self.client.force_login(self.waiter)
        response = self.client.post(
            reverse(
                "staff_order_edit_remove_item",
                kwargs={"public_code": self.order.public_code, "item_id": self.item.pk},
            ),
            {"next": reverse("staff_home")},
        )

        self.assertRedirects(response, reverse("staff_home"), fetch_redirect_response=False)
        self.assertFalse(OrderItem.objects.filter(pk=self.item.pk).exists())

    def test_quick_cancel_returns_to_workspace_and_preserves_order(self):
        self.client.force_login(self.waiter)
        response = self.client.post(
            reverse("staff_order_status", kwargs={"public_code": self.order.public_code}),
            {
                "status": Order.Status.CANCELLED,
                "cancellation_reason": "staff_mistake",
                "cancellation_notes": "اختبار تعديل سريع",
                "next": reverse("staff_home"),
            },
        )

        self.assertRedirects(response, reverse("staff_home"), fetch_redirect_response=False)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELLED)
        self.assertEqual(self.order.cancellation_reason, "staff_mistake")
        self.assertTrue(Order.objects.filter(pk=self.order.pk).exists())

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


    def test_specialist_screens_share_one_role_gated_context_drawer(self):
        self.client.force_login(self.admin)
        for name in ('staff_home', 'staff_orders', 'staff_visits', 'staff_cashier'):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'id="staff-context-drawer"', count=1)
                self.assertContains(response, 'js/staff_workspace.js', count=1)
                self.assertContains(response, 'css/staff_ux31.css')

        cashier_url = reverse('staff_cashier_order', kwargs={'public_code': self.visit.public_code})
        cashier_page = self.client.get(reverse('staff_cashier'))
        self.assertContains(
            cashier_page,
            f'data-staff-context-url="{cashier_url}?panel=payment"',
        )

    def test_cashier_only_can_open_payment_sheet_from_cashier_list(self):
        self.client.force_login(self.cashier)
        page = self.client.get(reverse('staff_cashier'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'id="staff-context-drawer"', count=1)
        self.assertContains(page, 'data-context-title="قبض حساب الجلسة"')
        payment = self.client.get(
            reverse('staff_cashier_order', kwargs={'public_code': self.visit.public_code}),
            {'panel': 'payment'},
        )
        self.assertTemplateUsed(payment, 'staff/_visit_payment_panel.html')

    def test_orders_and_visits_lists_open_details_in_drawer(self):
        self.client.force_login(self.admin)
        edit_url = reverse('staff_order_edit', kwargs={'public_code': self.order.public_code})
        visit_url = reverse('staff_visit_detail', kwargs={'public_code': self.visit.public_code})
        orders = self.client.get(reverse('staff_orders'))
        visits = self.client.get(reverse('staff_visits'))
        self.assertContains(orders, f'data-staff-context-url="{edit_url}?panel=context"')
        self.assertContains(visits, f'data-visit-context-url="{visit_url}?panel=1"')

    def test_order_editor_has_note_form_without_leaving_drawer(self):
        self.client.force_login(self.waiter)
        url = reverse('staff_order_edit', kwargs={'public_code': self.order.public_code})
        fragment = self.client.get(url, {'panel': 'context'})
        item_url = reverse(
            'staff_order_edit_update_item',
            kwargs={'public_code': self.order.public_code, 'item_id': self.item.pk},
        )
        self.assertContains(fragment, 'تعديل الكمية أو الملاحظة')
        self.assertContains(fragment, f'hx-post="{item_url}?panel=context"')
        self.assertContains(fragment, 'name="item_note"')
        self.assertContains(fragment, 'name="edit_scope" value="basic"')

        response = self.client.post(
            item_url + '?panel=context',
            {'quantity': '2', 'item_note': 'بدون سكر', 'edit_scope': 'basic'},
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'staff/_order_panel.html')
        self.assertEqual(response['HX-Trigger'], 'hub:staff-context-updated')
        self.assertContains(response, 'بدون سكر')
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 2)
        self.assertEqual(self.item.item_note, 'بدون سكر')
        self.assertTrue(ActivityLog.objects.filter(action='order_item_updated').exists())

    def test_order_removal_stays_in_drawer_and_is_audited(self):
        self.client.force_login(self.waiter)
        url = reverse('staff_order_edit_remove_item', kwargs={
            'public_code': self.order.public_code, 'item_id': self.item.pk,
        })
        response = self.client.post(url + '?panel=context', {}, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'staff/_order_panel.html')
        self.assertEqual(response['HX-Trigger'], 'hub:staff-context-updated')
        self.assertFalse(OrderItem.objects.filter(pk=self.item.pk).exists())
        self.assertTrue(ActivityLog.objects.filter(action='order_item_removed').exists())

    def test_visit_account_drawer_changes_table_with_existing_audit_path(self):
        self.client.force_login(self.admin)
        destination = TableArea.objects.create(room=self.room, name_ar='طاولة 9')
        url = reverse('staff_visit_detail', kwargs={'public_code': self.visit.public_code})
        panel = self.client.get(url, {'panel': '1'})
        self.assertContains(panel, 'تعديل الطاولة أو العضو')
        self.assertContains(panel, f'hx-post="{url}?panel=1"')
        self.assertContains(panel, 'name="action" value="change_table"')
        self.assertContains(panel, 'name="action" value="attach_member"')

        changed = self.client.post(
            url + '?panel=1',
            {'action': 'change_table', 'table': str(destination.pk)},
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(changed.status_code, 200)
        self.assertTemplateUsed(changed, 'staff/_visit_panel.html')
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.table_id, destination.pk)
        self.assertContains(changed, destination.name_ar)
        self.assertTrue(ActivityLog.objects.filter(action='visit.table_changed').exists())

    def test_empty_visit_delete_is_available_but_nonempty_stays_protected(self):
        self.client.force_login(self.admin)
        url = reverse('staff_visit_detail', kwargs={'public_code': self.visit.public_code})
        nonempty = self.client.get(url, {'panel': '1'})
        self.assertNotContains(nonempty, 'name="action" value="delete_empty"')
        empty = HubVisit.objects.create(table=self.table)
        empty_url = reverse('staff_visit_detail', kwargs={'public_code': empty.public_code})
        panel = self.client.get(empty_url, {'panel': '1'})
        self.assertContains(panel, 'name="action" value="delete_empty"')
        self.assertContains(panel, 'تأكيد حذف الجلسة الفارغة')
        deleted = self.client.post(empty_url, {'action': 'delete_empty'})
        self.assertEqual(deleted.status_code, 302)
        self.assertFalse(HubVisit.objects.filter(pk=empty.pk).exists())

    def test_plain_post_and_full_page_paths_remain_available(self):
        self.client.force_login(self.waiter)
        url = reverse('staff_order_edit_update_item', kwargs={
            'public_code': self.order.public_code, 'item_id': self.item.pk,
        })
        result = self.client.post(url, {
            'quantity': '2', 'item_note': 'اختبار كامل',
            'edit_scope': 'basic', 'next': reverse('staff_home'),
        })
        self.assertRedirects(result, reverse('staff_home'), fetch_redirect_response=False)
        self.assertNotIn('HX-Trigger', result)

    def test_kitchen_workspace_exposes_operational_drawer_but_not_payment_controls(self):
        self.client.force_login(self.kitchen)
        response = self.client.get(reverse("staff_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="staff-context-drawer"')
        self.assertContains(response, "data-staff-context-url")
        self.assertContains(response, ">تعديل</a>", html=False)
        self.assertNotContains(response, 'data-context-title="الدفع وإغلاق الحساب"')
