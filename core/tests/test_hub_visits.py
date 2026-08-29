import hashlib

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from catalog.models import MenuSection
from core.models import ActivityLog, Category, HubVisit, HubVisitBrowserCredential, Order, Payment, Product, Room, SystemSetting, TableArea
from core.settings_helpers import get_system_settings


TEST_STATIC_STORAGE = {
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False,
                   STORAGES=TEST_STATIC_STORAGE)
class HubVisitPublicTests(TestCase):
    def setUp(self):
        self.room = Room.objects.create(name_ar='الصالة')
        self.table = TableArea.objects.create(room=self.room, name_ar='طاولة 1')
        self.category = Category.objects.create(name_ar='مشروبات')
        self.section = MenuSection.objects.create(name_ar='القائمة')
        self.product = Product.objects.create(category=self.category, name_ar='قهوة', price_syp=1000, is_available=True, visible_on_qr=True, orderable_on_qr=True)
        self.product.menu_sections.add(self.section)
        self.setting = SystemSetting.objects.create(customer_visits_enabled=False)
        get_system_settings.cache_clear()
        self.url = reverse('menu_table', kwargs={'qr_token': self.table.qr_token})

    def tearDown(self):
        get_system_settings.cache_clear()

    def payload(self):
        return {f'qty_{self.product.pk}': '1', 'fulfillment_mode': Order.FulfillmentMode.TABLE}

    def enable(self):
        self.setting.customer_visits_enabled = True
        self.setting.save(update_fields=['customer_visits_enabled', 'updated_at'])
        get_system_settings.cache_clear()

    def bind_new_visit(self, client=None, table=None):
        client = client or self.client
        table = table or self.table
        url = reverse('menu_table', kwargs={'qr_token': table.qr_token})
        response = client.post(url, {'visit_action': 'create'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], url)
        self.assertIn('hub_visit', client.cookies)
        return response

    def test_disabled_preserves_standalone_public_order(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(Order.objects.get().visit)
        self.assertFalse(HubVisit.objects.exists())
        self.assertNotIn('hub_visit', response.cookies)
        confirmation = self.client.get(response['Location'])
        self.assertContains(confirmation, 'تم استلام طلبك')
        self.assertContains(confirmation, reverse('order_qr', kwargs={
            'public_code': Order.objects.get().public_code,
        }))
        self.assertNotContains(confirmation, 'تم إرسال الطلب')

    def test_scan_does_not_create_visit_and_account_selection_does(self):
        self.enable()
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertFalse(HubVisit.objects.exists())

        selection = self.bind_new_visit()
        visit = HubVisit.objects.get()
        raw = selection.cookies['hub_visit'].value
        credential = HubVisitBrowserCredential.objects.get()
        self.assertNotEqual(credential.token_hash, raw)
        self.assertEqual(credential.token_hash, hashlib.sha256(raw.encode()).hexdigest())
        self.assertTrue(selection.cookies['hub_visit']['httponly'])
        self.assertEqual(selection.cookies['hub_visit']['samesite'], 'Lax')

        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], self.url)
        self.assertEqual(Order.objects.get().visit, visit)

    def test_failed_order_keeps_selected_visit_and_same_browser_reuses_it(self):
        self.enable()
        self.bind_new_visit()
        selected = HubVisit.objects.get()

        failed = self.client.post(self.url, {'fulfillment_mode': 'table'})
        self.assertEqual(failed.status_code, 200)
        self.assertEqual(HubVisit.objects.count(), 1)
        self.assertTrue(HubVisit.objects.filter(pk=selected.pk, status=HubVisit.Status.OPEN).exists())
        self.assertFalse(Order.objects.exists())

        self.client.post(self.url, self.payload())
        self.client.post(self.url, self.payload())
        self.assertEqual(HubVisit.objects.count(), 1)
        self.assertEqual(Order.objects.count(), 2)
        self.assertEqual(Order.objects.values('visit_id').distinct().count(), 1)
        entry = self.client.get(self.url)
        self.assertContains(entry, 'جلستك')
        self.assertContains(entry, f'href="{reverse("current_visit")}"')
        self.assertEqual(self.client.get(reverse('current_visit')).status_code, 200)

    def test_visit_order_returns_to_menu_and_session_tracks_whole_account(self):
        self.enable()
        self.bind_new_visit()
        first = self.client.post(self.url, self.payload())
        first_order = Order.objects.get()
        self.assertEqual(first['Location'], self.url)

        menu = self.client.get(first['Location'])
        self.assertContains(menu, 'تم إرسال الطلب')
        self.assertContains(menu, reverse('current_visit'))
        self.assertContains(menu, self.product.name_ar)
        self.assertNotContains(menu, reverse('order_qr', kwargs={
            'public_code': first_order.public_code,
        }))
        self.assertNotContains(menu, 'احتفظ برمز QR')

        self.client.post(self.url, self.payload())
        second_order = Order.objects.exclude(pk=first_order.pk).get()
        Payment.objects.create(order=first_order, amount_syp=500,
                               method=Payment.Method.CASH)
        page = self.client.get(reverse('current_visit'))
        self.assertContains(page, 'جلستي')
        self.assertContains(page, 'طلباتي')
        self.assertContains(page, first_order.display_number)
        self.assertContains(page, second_order.display_number)
        self.assertLess(page.content.index(second_order.display_number.encode()),
                        page.content.index(first_order.display_number.encode()))
        self.assertContains(page, '2,000 ل.س')
        self.assertContains(page, '500 ل.س')
        self.assertContains(page, '1,500 ل.س')
        self.assertContains(page, self.table.name_ar)
        self.assertContains(page, self.room.name_ar)
        self.assertContains(page, f'href="{self.url}"')
        self.assertNotContains(page, '?view=menu')

    def test_same_browser_different_table_selects_separate_visit(self):
        self.enable()
        first_selection = self.bind_new_visit()
        self.client.post(self.url, self.payload())
        first_visit = Order.objects.get().visit

        other_table = TableArea.objects.create(room=self.room, name_ar='طاولة 2')
        other_url = reverse('menu_table', kwargs={'qr_token': other_table.qr_token})
        second_selection = self.bind_new_visit(table=other_table)
        self.client.post(other_url, self.payload())
        second_visit = Order.objects.exclude(visit=first_visit).get().visit

        self.assertNotEqual(first_visit, second_visit)
        self.assertNotEqual(first_selection.cookies['hub_visit'].value,
                            second_selection.cookies['hub_visit'].value)
        current = self.client.get(reverse('current_visit'))
        self.assertContains(current, other_table.name_ar)
        self.assertNotContains(current, self.table.name_ar)

    def test_two_browsers_can_open_separate_visits_on_same_table(self):
        self.enable()
        self.bind_new_visit()
        self.client.post(self.url, self.payload())
        visit_a = Order.objects.get().visit

        other = Client()
        page = other.get(self.url)
        self.assertContains(page, 'فتح حساب منفصل')
        self.bind_new_visit(client=other)
        other.post(self.url, self.payload())

        self.assertEqual(HubVisit.objects.count(), 2)
        self.assertNotEqual(Order.objects.exclude(visit=visit_a).get().visit, visit_a)

    def test_invalid_and_closed_credentials_cannot_access_dashboard(self):
        self.enable()
        self.client.cookies['hub_visit'] = 'invalid-token'
        self.assertRedirects(self.client.get(reverse('current_visit')), reverse('menu_public'))

        selection = self.bind_new_visit()
        raw = selection.cookies['hub_visit'].value
        visit = HubVisit.objects.get()
        visit.status = HubVisit.Status.CLOSED
        visit.save(update_fields=['status'])
        self.client.cookies['hub_visit'] = raw
        self.assertRedirects(self.client.get(reverse('current_visit')), reverse('menu_public'))


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False,
                   STORAGES=TEST_STATIC_STORAGE)
class HubVisitStaffTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username='visit-admin', password='pass', email='v@example.com', phone='+963900001111')
        self.client.force_login(self.user)
        self.room = Room.objects.create(name_ar='الصالة')
        self.table = TableArea.objects.create(room=self.room, name_ar='طاولة')
        self.category = Category.objects.create(name_ar='مشروبات')
        self.product = Product.objects.create(category=self.category, name_ar='ماء', price_syp=500)

    def test_manual_create_attach_payment_aggregate_and_close(self):
        response = self.client.post(reverse('staff_visits'), {'table': self.table.pk, 'notes': 'يدوي'})
        visit = HubVisit.objects.get()
        self.assertRedirects(response, reverse('staff_visit_detail', kwargs={'public_code': visit.public_code}))
        order = Order.objects.create(table=self.table)
        from core.models import OrderItem
        OrderItem.objects.create(order=order, product=self.product, quantity=1, product_name_ar_snapshot='ماء', unit_price_syp_snapshot=500, line_total_syp_snapshot=500)
        self.client.post(reverse('staff_visit_detail', kwargs={'public_code': visit.public_code}), {'action': 'attach_order', 'order_code': order.public_code})
        order.refresh_from_db(); self.assertEqual(order.visit, visit)
        self.assertEqual(visit.remaining_syp, 500)
        self.client.post(reverse('staff_visit_detail', kwargs={'public_code': visit.public_code}), {'action': 'close'})
        visit.refresh_from_db(); self.assertEqual(visit.status, 'open')
        Payment.objects.create(order=order, amount_syp=500, method=Payment.Method.CASH, created_by=self.user)
        self.assertEqual(visit.paid_syp, 500); self.assertEqual(visit.remaining_syp, 0)
        self.client.post(reverse('staff_visit_detail', kwargs={'public_code': visit.public_code}), {'action': 'close'})
        visit.refresh_from_db(); self.assertEqual(visit.status, 'closed')
        self.assertTrue(ActivityLog.objects.filter(action='visit.closed').exists())

    def test_standalone_order_remains_valid(self):
        order = Order.objects.create()
        self.assertIsNone(order.visit)
