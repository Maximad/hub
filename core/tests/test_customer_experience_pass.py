from pathlib import Path

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    ActivityLog,
    Category,
    HubVisit,
    InternetSession,
    NotificationEvent,
    Order,
    OrderItem,
    Product,
    Payment,
    PostingBatch,
    Room,
    SystemSetting,
    TableArea,
)
from core.services.visits import issue_visit_credential
from core.settings_helpers import get_system_settings
from internet.models import InternetSessionBrowserBinding


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class CustomerServiceRequestTests(TestCase):
    def setUp(self):
        get_system_settings.cache_clear()
        self.settings = SystemSetting.objects.create(customer_visits_enabled=True)
        get_system_settings.cache_clear()
        room = Room.objects.create(name_ar='الجنينة')
        self.table = TableArea.objects.create(room=room, name_ar='طاولة 4')
        self.visit = HubVisit.objects.create(table=self.table)
        category = Category.objects.create(name_ar='مشروبات')
        product = Product.objects.create(category=category, name_ar='قهوة', price_syp=100)
        self.order = Order.objects.create(
            visit=self.visit,
            table=self.table,
            fulfillment_mode=Order.FulfillmentMode.TABLE,
            service_mode=Order.ServiceMode.TABLE,
        )
        OrderItem.objects.create(
            order=self.order,
            product=product,
            product_name_ar_snapshot=product.name_ar,
            unit_price_syp_snapshot=100,
            quantity=2,
            line_total_syp_snapshot=200,
        )
        _credential, raw_token = issue_visit_credential(self.visit)
        self.client.cookies['hub_visit'] = raw_token

    def tearDown(self):
        get_system_settings.cache_clear()

    def test_bill_request_notifies_cashier_and_is_throttled(self):
        url = reverse('customer_visit_request')
        response = self.client.post(url, {'action': 'bill'})
        self.assertEqual(response.status_code, 302)
        event = NotificationEvent.objects.get(event_type='payment_pending')
        self.assertEqual(event.target_role, 'cashier')
        self.assertEqual(event.order, self.order)
        self.assertIn('تطلب الحساب', event.title_ar)
        self.assertEqual(ActivityLog.objects.filter(action='customer.bill_requested').count(), 1)

        second = self.client.post(url, {'action': 'bill'})
        self.assertEqual(second.status_code, 302)
        self.assertEqual(NotificationEvent.objects.filter(event_type='payment_pending').count(), 1)
        self.assertEqual(ActivityLog.objects.filter(action='customer.bill_requested').count(), 1)

    def test_assistance_request_targets_service(self):
        response = self.client.post(reverse('customer_visit_request'), {'action': 'assistance'})
        self.assertEqual(response.status_code, 302)
        event = NotificationEvent.objects.get(event_type='order_edited')
        self.assertEqual(event.target_role, 'service')
        self.assertIn('مساعدة مطلوبة', event.title_ar)
        self.assertEqual(ActivityLog.objects.filter(action='customer.assistance_requested').count(), 1)

    def metered_visit(self):
        visit = HubVisit.objects.create()
        credential, token = issue_visit_credential(visit)
        self.client.cookies['hub_visit'] = token
        session = InternetSession.objects.create(
            visit=visit, start_time=timezone.now(), rate_per_hour_syp=100,
            billing_mode=InternetSession.BillingMode.OPEN_METERED,
        )
        InternetSessionBrowserBinding.objects.create(session=session, credential=credential)
        return visit, session

    def test_metered_checkout_without_order_notifies_once_without_billing(self):
        visit, session = self.metered_visit()
        before = (Order.objects.count(), Payment.objects.count(), PostingBatch.objects.count())
        page = self.client.get(reverse('current_visit'))
        self.assertContains(page, 'name="action" value="bill"')
        self.assertTrue(page.context['can_request_bill'])
        url = reverse('customer_visit_request')
        for _ in range(2):
            self.assertEqual(self.client.post(url, {'action': 'bill'}).status_code, 302)
        event = NotificationEvent.objects.get(event_type='payment_pending')
        self.assertEqual(event.target_role, 'cashier')
        self.assertIsNone(event.order_id)
        self.assertIn(str(visit.display_number), event.title_ar)
        self.assertIn(str(visit.display_number), event.message_ar)
        self.assertEqual(ActivityLog.objects.filter(action='customer.bill_requested').count(), 1)
        session.refresh_from_db()
        visit.refresh_from_db()
        self.assertEqual(session.status, InternetSession.Status.ACTIVE)
        self.assertIsNone(session.end_time)
        self.assertIsNone(session.linked_order_id)
        self.assertIsNone(session.linked_payment_id)
        self.assertEqual(visit.status, HubVisit.Status.OPEN)
        self.assertEqual(before, (Order.objects.count(), Payment.objects.count(), PostingBatch.objects.count()))

    def test_metered_checkout_requires_own_browser_and_visit(self):
        visit, session = self.metered_visit()
        for other_visit in [visit, HubVisit.objects.create()]:
            _credential, token = issue_visit_credential(other_visit)
            self.client.cookies['hub_visit'] = token
            page = self.client.get(reverse('current_visit'))
            self.assertFalse(page.context['can_request_bill'])
            self.client.post(reverse('customer_visit_request'), {'action': 'bill'})
        self.client.cookies.clear()
        self.client.post(reverse('customer_visit_request'), {'action': 'bill'})
        self.assertFalse(NotificationEvent.objects.filter(event_type='payment_pending').exists())

    def test_free_or_ended_internet_does_not_enable_empty_bill_request(self):
        _visit, session = self.metered_visit()
        for mode, status in [
            (InternetSession.BillingMode.FREE, InternetSession.Status.ACTIVE),
            (InternetSession.BillingMode.OPEN_METERED, InternetSession.Status.ENDED),
        ]:
            session.billing_mode, session.status = mode, status
            session.save(update_fields=['billing_mode', 'status'])
            page = self.client.get(reverse('current_visit'))
            self.assertFalse(page.context['can_request_bill'])
            self.client.post(reverse('customer_visit_request'), {'action': 'bill'})
        self.assertFalse(NotificationEvent.objects.filter(event_type='payment_pending').exists())

    def test_closed_metered_visit_cannot_request_checkout(self):
        visit, _session = self.metered_visit()
        visit.status = HubVisit.Status.CLOSED
        visit.save(update_fields=['status'])
        self.client.post(reverse('customer_visit_request'), {'action': 'bill'})
        self.assertFalse(NotificationEvent.objects.filter(event_type='payment_pending').exists())

    def test_public_menu_does_not_render_staff_asset_bundle(self):
        response = self.client.get(reverse('menu_public'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'customer_experience_v2.css')
        self.assertContains(response, 'customer_space.js')
        self.assertNotContains(response, 'staff_workspace.css')
        self.assertNotContains(response, 'staff_ui_v2.js')
        self.assertNotContains(response, 'staff_notifications.js')


class CustomerExperienceStaticContractTests(SimpleTestCase):
    def test_customer_navigation_injects_menu_search_and_stays_three_actions_or_less(self):
        root = Path(__file__).resolve().parents[2]
        javascript = (root / 'static/js/customer_space.js').read_text()
        self.assertIn('data-pos-search', javascript)
        self.assertIn('ابحث في المنيو', javascript)
        self.assertIn("navItem('المنيو'", javascript)
        self.assertIn("hasVisit ? navItem('جلستي'", javascript)
        self.assertNotIn("navItem('الخدمات'", javascript)

    def test_session_page_has_bill_help_and_customer_friendly_progress(self):
        root = Path(__file__).resolve().parents[2]
        template = (root / 'templates/menu/current_visit.html').read_text()
        self.assertIn("name=\"action\" value=\"bill\"", template)
        self.assertIn("name=\"action\" value=\"assistance\"", template)
        self.assertIn('تم الاستلام', template)
        self.assertIn('قيد التحضير', template)
        self.assertIn('جاهز', template)
        self.assertIn('تم التقديم', template)
        self.assertNotIn('position:sticky;top:.5rem', template)

    def test_internet_status_only_claims_service_readiness_not_verified_device_connection(self):
        root = Path(__file__).resolve().parents[2]
        session_page = (root / 'templates/menu/current_visit.html').read_text()
        wifi_page = (root / 'templates/menu/wifi_entry.html').read_text()

        self.assertIn('أساسي · مفعّل', session_page)
        self.assertIn('سريع · مفعّل', session_page)
        self.assertIn('تحقّق من اتصال جهازك بالشبكة', session_page)
        self.assertNotIn('أساسي · متصل', session_page)
        self.assertNotIn('سريع · متصل', session_page)
        self.assertIn('الإنترنت السريع جاهز', wifi_page)
        self.assertIn('الإنترنت الأساسي جاهز', wifi_page)
        self.assertIn('تحقّق من اتصال جهازك بالإنترنت', wifi_page)
        self.assertNotIn('تم الاتصال', wifi_page)

    def test_customer_styles_hide_legacy_internet_storefront_on_menu(self):
        root = Path(__file__).resolve().parents[2]
        stylesheet = (root / 'static/css/customer_experience_v2.css').read_text()
        self.assertIn('body.public-menu-page .menu-public > #internet', stylesheet)
        self.assertIn('.customer-menu-search', stylesheet)
        self.assertIn('.customer-order-progress', stylesheet)
