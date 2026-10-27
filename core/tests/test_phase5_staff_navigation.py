"""Phase 5 staff navigation and presentation contracts.

Verifies existing permissions/routes while pages are consolidated visually.
No finance, stock, Internet or order business logic is replaced.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(ALLOWED_HOSTS=["testserver"], SECURE_SSL_REDIRECT=False)
class Phase5StaffNavigationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_user(
            username="ux5-admin", phone="985501", password="test", role="admin",
        )
        cls.waiter = User.objects.create_user(
            username="ux5-waiter", phone="985502", password="test", role="waiter",
        )

    def test_operations_directory_is_grouped_with_known_destinations(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("staff_home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="staff-today"')
        self.assertContains(response, 'id="staff-more"')
        self.assertContains(response, "إدارة القائمة")
        self.assertContains(response, "المستخدمون والصلاحيات")
        self.assertContains(response, reverse("staff_finance_home"))
        self.assertContains(response, reverse("staff_inventory_home"))
        self.assertContains(response, "staff_ux5.css")
        self.assertContains(response, 'href="#hub-main-content"')

    def test_waiter_navigation_does_not_promote_manager_modules(self):
        self.client.force_login(self.waiter)
        response = self.client.get(reverse("staff_home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "الحجوزات والوصول")
        self.assertNotContains(response, "المستخدمون والصلاحيات")
        self.assertNotContains(response, "تقارير الإدارة")
        self.assertNotContains(response, "المالية والحسابات")

    def test_finance_nav_keeps_advanced_routes_and_selected_report_date(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("staff_finance_home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "عمليات مالية أخرى")
        self.assertContains(response, reverse("staff_finance_transfers"))
        self.assertContains(response, reverse("staff_purchases"))
        self.assertContains(response, 'aria-label="الأعمال المالية اليومية"')
        self.assertContains(response, 'name="date"')
        self.assertContains(response, "ملخص الصندوق والمبيعات")

    def test_inventory_keeps_details_and_shortcuts(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("staff_inventory_home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "الوصفات والحركات والتقارير")
        self.assertContains(response, reverse("staff_prep_batches"))
        self.assertContains(response, reverse("staff_inventory_low_stock"))
        self.assertContains(response, "تسجيل شراء")

    def test_reports_and_specialist_indexes_render_without_write_side_effects(self):
        self.client.force_login(self.admin)
        for route in ("staff_reports_home", "staff_events", "staff_members", "staff_orders"):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)
        response = self.client.get(reverse("staff_visits"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "الجلسات والحسابات")
        self.assertNotContains(response, '<main dir="rtl">')

    def test_anonymous_user_cannot_access_consolidated_staff_home(self):
        response = self.client.get(reverse("staff_home"))
        self.assertEqual(response.status_code, 302)

    def test_redundant_operations_help_is_removed(self):
        self.client.force_login(self.waiter)
        response = self.client.get(reverse("staff_home"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="staff-help"')
        self.assertNotContains(response, 'href="/staff/#staff-help"')
        self.assertContains(response, 'id="staff-more"')

    def test_pos_explains_shared_versus_separate_bills(self):
        self.client.force_login(self.waiter)
        response = self.client.get(reverse("staff_pos"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-describedby="pos-account-help"')
        self.assertContains(response, "حساب منفصل لهذا الطلب")
        self.assertContains(response, "اختيار الطاولة وحده لا يضيف الطلب إلى الحساب.")
        self.assertContains(response, "تسجيل الطلبات")

    def test_cashier_explains_shared_bill_without_technical_session_model(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("staff_cashier"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تُجمع الطلبات المرتبطة بحساب مفتوح في فاتورة واحدة.")

    def test_staff_labels_remove_unnecessary_technical_terms(self):
        from pathlib import Path
        from django.conf import settings

        root = Path(settings.BASE_DIR)
        pos = (root / "templates/staff/pos.html").read_text(encoding="utf-8")
        cashier = (root / "templates/staff/cashier.html").read_text(encoding="utf-8")
        stock = (root / "templates/staff/inventory_stock_count.html").read_text(encoding="utf-8")
        self.assertIn("الإضافات والتعديلات", pos)
        self.assertIn("> سفري</label>", pos)
        self.assertIn("طلبات بحساب منفصل", cashier)
        self.assertIn("لا يسجّل شراءً أو دفعاً أو قيمة مالية للمخزون", stock)
        self.assertNotIn("قيد تقييم افتتاحي مالي", stock)
