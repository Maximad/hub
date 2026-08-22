from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import HubVisit, Order, Room, SystemSetting, TableArea
from core.settings_helpers import get_system_settings


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class StaffWorkspaceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='workspace-admin', phone='92001', password='x', role='admin'
        )
        self.waiter = User.objects.create_user(
            username='workspace-waiter', phone='92002', password='x', role='waiter'
        )
        self.kitchen = User.objects.create_user(
            username='workspace-kitchen', phone='92003', password='x', role='kitchen'
        )
        self.room = Room.objects.create(name_ar='الصالة')
        self.table = TableArea.objects.create(room=self.room, name_ar='طاولة 7')
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

    def test_staff_home_is_live_workspace_not_module_directory(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('staff_home'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'staff/home.html')
        self.assertContains(response, 'مساحة العمليات')
        self.assertContains(response, 'الجلسات والطاولات المفتوحة')
        self.assertContains(response, self.table.name_ar)
        self.assertContains(response, self.order.display_number)
        self.assertContains(response, '+ طلب جديد')
        self.assertNotContains(response, 'وصول سريع إلى مساحات التشغيل اليومية حسب صلاحياتك.')

    def test_staff_pages_share_persistent_navigation_shell(self):
        self.client.force_login(self.waiter)
        response = self.client.get(reverse('staff_orders'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'التنقل التشغيلي')
        self.assertContains(response, 'الطلبات')
        self.assertContains(response, 'الجلسات')
        self.assertContains(response, 'الحجوزات')
        self.assertNotContains(response, '>المالية</a>')
        self.assertNotContains(response, '>التقارير</a>')

    def test_kitchen_workspace_hides_customer_and_cashier_actions(self):
        self.client.force_login(self.kitchen)
        response = self.client.get(reverse('staff_home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'فتح لوحة التحضير')
        self.assertNotContains(response, '+ طلب جديد')
        self.assertNotContains(response, '+ جلسة جديدة')
        self.assertNotContains(response, '>الدفع</a>')
        self.assertNotContains(response, '>الحجوزات</a>')
