from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Category,
    HubVisit,
    HubVisitBrowserCredential,
    InternetSession,
    Order,
    Product,
    Room,
    SystemSetting,
    TableArea,
)
from core.services.visit_internet_devices import start_visit_metered_session
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

        category = Category.objects.create(name_ar='خدمات')
        self.internet_product = Product.objects.create(
            category=category,
            name_ar='إنترنت حسب الوقت',
            price_syp=0,
            product_type=Product.ProductType.INTERNET,
            item_type=Product.ItemType.SERVICE,
            service_type=Product.ServiceType.INTERNET,
            requires_preparation=False,
            visible_on_qr=False,
            orderable_on_qr=False,
            visible_on_pos=False,
            orderable_on_pos=False,
            not_discountable=True,
            track_margin=False,
        )
        SystemSetting.objects.create(
            customer_visits_enabled=True,
            customer_internet_self_service_enabled=True,
            internet_metered_enabled=True,
            allow_guest_internet_sessions=True,
            default_rate_per_hour_syp=600,
            default_minimum_minutes=30,
            default_rounding_increment_minutes=15,
            default_minimum_charge_syp=0,
            default_free_grace_minutes=0,
            auto_create_order_for_metered_sessions=True,
            internet_service_product=self.internet_product,
        )
        get_system_settings.cache_clear()

    def tearDown(self):
        get_system_settings.cache_clear()

    def _start_metered(self, visit=None):
        visit = visit or self.visit
        credential = HubVisitBrowserCredential.objects.create(
            visit=visit,
            token_hash=(f'{visit.pk:064x}')[-64:],
        )
        session, created = start_visit_metered_session(
            visit=visit,
            credential=credential,
        )
        self.assertTrue(created)
        started = timezone.now() - timedelta(minutes=31)
        InternetSession.objects.filter(pk=session.pk).update(
            start_time=started,
            started_at=started,
        )
        session.refresh_from_db()
        return session

    def test_staff_home_is_unified_front_of_house_workspace(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('staff_home'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'staff/home.html')
        self.assertContains(response, '>العمليات</h2>', html=False)
        self.assertContains(response, 'الطلبات، الحساب، الدفع والإنترنت من مساحة واحدة.')
        self.assertContains(response, 'الحسابات والطاولات المفتوحة')
        self.assertContains(response, self.table.name_ar)
        self.assertContains(response, self.order.display_number)
        self.assertContains(response, '+ طلب جديد')
        self.assertContains(response, '+ فتح حساب / جلسة جديدة')
        self.assertContains(response, 'data-context-title="الدفع وإغلاق الحساب"')
        self.assertContains(response, 'data-context-title="إنترنت الحساب"')
        self.assertNotContains(response, 'وصول سريع إلى مساحات التشغيل اليومية حسب صلاحياتك.')

    def test_visit_card_progressively_opens_unified_account_drawer(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('staff_home'))

        detail_url = reverse('staff_visit_detail', kwargs={'public_code': self.visit.public_code})
        self.assertContains(response, 'id="staff-context-drawer"')
        self.assertContains(response, 'data-visit-context-url="{}?panel=1"'.format(detail_url))
        self.assertContains(response, 'js/staff_workspace.js')

    def test_visit_panel_contains_order_payment_and_internet_actions(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('staff_visit_detail', kwargs={'public_code': self.visit.public_code}),
            {'panel': '1'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'staff/_visit_panel.html')
        self.assertContains(response, 'حساب الزبون')
        self.assertContains(response, self.table.name_ar)
        self.assertContains(response, '+ إضافة طلب')
        self.assertContains(response, 'الدفع')
        self.assertContains(response, 'الإنترنت')
        self.assertContains(response, '?panel=internet')
        self.assertContains(response, self.order.display_number)
        self.assertNotContains(response, '<!DOCTYPE html>')

    def test_context_bound_pos_preselects_requested_open_visit(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('staff_pos'),
            {'visit': str(self.visit.public_code)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_visit_id'], str(self.visit.pk))
        self.assertContains(response, 'value="{}" selected'.format(self.visit.pk))

    def test_staff_primary_navigation_is_reduced_to_operations_and_specialists(self):
        self.client.force_login(self.waiter)
        response = self.client.get(reverse('staff_orders'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'التنقل التشغيلي')
        self.assertContains(response, '>العمليات</a>', html=False)
        self.assertContains(response, '>المزيد</a>', html=False)
        self.assertNotContains(response, '>الجلسات</a>', html=False)
        self.assertNotContains(response, '>الدفع</a>', html=False)
        self.assertNotContains(response, '>الإنترنت</a>', html=False)
        self.assertNotContains(response, '>المالية</a>', html=False)
        self.assertNotContains(response, '>التقارير</a>', html=False)

    def test_kitchen_workspace_hides_customer_and_cashier_actions(self):
        self.client.force_login(self.kitchen)
        response = self.client.get(reverse('staff_home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'فتح لوحة التحضير')
        self.assertNotContains(response, '+ طلب جديد')
        self.assertNotContains(response, '+ فتح حساب / جلسة جديدة')
        self.assertNotContains(response, 'data-context-title="الدفع وإغلاق الحساب"')
        self.assertNotContains(response, 'data-context-title="إنترنت الحساب"')

    def test_staff_can_open_second_independent_account_on_same_table_from_operations(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('staff_visits'),
            {'table': self.table.pk, 'notes': 'حساب منفصل', 'next': 'workspace'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith('/staff/?visit='))
        self.assertEqual(
            HubVisit.objects.filter(table=self.table, status=HubVisit.Status.OPEN).count(),
            2,
        )

    def test_internet_panel_and_stop_action_are_scoped_to_selected_account(self):
        session = self._start_metered()
        other_visit = HubVisit.objects.create(table=self.table)
        other_session = self._start_metered(other_visit)
        self.client.force_login(self.admin)

        panel_url = reverse('staff_visit_detail', kwargs={'public_code': self.visit.public_code})
        panel = self.client.get(panel_url, {'panel': 'internet'})
        self.assertEqual(panel.status_code, 200)
        self.assertTemplateUsed(panel, 'staff/_visit_internet_panel.html')
        self.assertContains(panel, 'إنترنت الحساب')
        self.assertContains(panel, f'جلسة إنترنت #{session.pk}')
        self.assertNotContains(panel, f'جلسة إنترنت #{other_session.pk}')

        wrong = self.client.post(
            panel_url + '?panel=internet',
            {'action': 'internet_stop', 'session_id': other_session.pk},
        )
        self.assertEqual(wrong.status_code, 404)
        other_session.refresh_from_db()
        self.assertEqual(other_session.status, InternetSession.Status.ACTIVE)

        stopped = self.client.post(
            panel_url + '?panel=internet',
            {'action': 'internet_stop', 'session_id': session.pk},
        )
        self.assertEqual(stopped.status_code, 200)
        session.refresh_from_db()
        self.assertNotEqual(session.status, InternetSession.Status.ACTIVE)
        self.assertContains(stopped, 'تم إيقاف جلسة الإنترنت وتحديث الحساب')

    def test_payment_drawer_can_settle_final_internet_charge_and_close_visit(self):
        session = self._start_metered()
        self.client.force_login(self.admin)
        pay_url = reverse('staff_cashier_pay', kwargs={'public_code': self.visit.public_code})
        panel_url = reverse('staff_cashier_order', kwargs={'public_code': self.visit.public_code})

        panel = self.client.get(panel_url, {'panel': 'payment'})
        self.assertEqual(panel.status_code, 200)
        self.assertContains(panel, 'تسديد المتبقي وإغلاق الحساب')
        self.assertContains(panel, 'name="method"')
        self.assertContains(panel, 'جلسة إنترنت فعالة')

        settled = self.client.post(
            pay_url + '?panel=payment',
            {
                'action': 'settle_close',
                'method': 'cash',
                'notes': 'إغلاق من العمليات',
            },
        )
        self.assertEqual(settled.status_code, 200)
        self.visit.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(self.visit.status, HubVisit.Status.CLOSED)
        self.assertNotEqual(session.status, InternetSession.Status.ACTIVE)
        self.assertEqual(self.visit.remaining_syp, 0)
        self.assertContains(settled, 'تم تسديد كامل حساب الجلسة وإغلاقها')
