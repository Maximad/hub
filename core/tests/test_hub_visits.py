import hashlib

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from catalog.models import MenuSection
from core.models import ActivityLog, Category, HubVisit, HubVisitBrowserCredential, Order, Payment, Product, Room, SystemSetting, TableArea
from core.settings_helpers import get_system_settings


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False,
                   STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
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
        self.assertNotContains(confirmation, 'تمت إضافة طلبك إلى جلستك')

    def test_scan_does_not_create_visit_and_first_valid_order_does(self):
        self.enable()
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertFalse(HubVisit.objects.exists())
        response = self.client.post(self.url, self.payload())
        visit = HubVisit.objects.get()
        self.assertEqual(Order.objects.get().visit, visit)
        raw = response.cookies['hub_visit'].value
        credential = HubVisitBrowserCredential.objects.get()
        self.assertNotEqual(credential.token_hash, raw)
        self.assertEqual(credential.token_hash, hashlib.sha256(raw.encode()).hexdigest())
        self.assertTrue(response.cookies['hub_visit']['httponly'])
        self.assertEqual(response.cookies['hub_visit']['samesite'], 'Lax')

    def test_failed_order_creates_no_orphan_and_same_browser_reuses_visit(self):
        self.enable()
        failed = self.client.post(self.url, {'fulfillment_mode': 'table'})
        self.assertEqual(failed.status_code, 200)
        self.assertFalse(HubVisit.objects.exists())
        first = self.client.post(self.url, self.payload())
        self.client.cookies['hub_visit'] = first.cookies['hub_visit'].value
        self.client.post(self.url, self.payload())
        self.assertEqual(HubVisit.objects.count(), 1)
        self.assertEqual(Order.objects.count(), 2)
        self.assertEqual(Order.objects.values('visit_id').distinct().count(), 1)
        entry = self.client.get(self.url)
        self.assertContains(entry, 'جلستك')
        self.assertContains(entry, f'href="{reverse("current_visit")}"')
        self.assertEqual(self.client.get(reverse('current_visit')).status_code, 200)

    def test_visit_order_confirmation_and_current_visit_are_one_flow(self):
        self.enable()
        first = self.client.post(self.url, self.payload())
        first_order = Order.objects.get()
        confirmation = self.client.get(first['Location'])
        self.assertContains(confirmation, 'تمت إضافة طلبك إلى جلستك')
        self.assertContains(confirmation, 'عرض جلستك')
        self.assertContains(confirmation, 'طلب المزيد')
        self.assertContains(confirmation, 'إجمالي جلستك')
        self.assertContains(confirmation, '1,000 ل.س')
        self.assertNotContains(confirmation, reverse('order_qr', kwargs={
            'public_code': first_order.public_code,
        }))
        self.assertNotContains(confirmation, 'احتفظ برمز QR')

        second = self.client.post(self.url, self.payload())
        second_order = Order.objects.exclude(pk=first_order.pk).get()
        Payment.objects.create(order=first_order, amount_syp=500,
                               method=Payment.Method.CASH)
        page = self.client.get(reverse('current_visit'))
        self.assertContains(page, 'جلستك اليوم')
        self.assertContains(page, first_order.display_number)
        self.assertContains(page, second_order.display_number)
        self.assertLess(page.content.index(second_order.display_number.encode()),
                        page.content.index(first_order.display_number.encode()))
        self.assertContains(page, '2,000 ل.س')
        self.assertContains(page, '500 ل.س')
        self.assertContains(page, '1,500 ل.س')
        self.assertContains(page, self.table.name_ar)
        self.assertContains(page, self.room.name_ar)
        self.assertContains(page, f'href="{self.url}?view=menu"')

    def test_same_browser_different_table_gets_separate_visit(self):
        self.enable()
        first = self.client.post(self.url, self.payload())
        first_visit = Order.objects.get().visit
        other_table = TableArea.objects.create(room=self.room, name_ar='طاولة 2')
        other_url = reverse('menu_table', kwargs={'qr_token': other_table.qr_token})
        second = self.client.post(other_url, self.payload())
        second_visit = Order.objects.exclude(visit=first_visit).get().visit
        self.assertNotEqual(first_visit, second_visit)
        self.assertNotEqual(first.cookies['hub_visit'].value,
                            second.cookies['hub_visit'].value)
        current = self.client.get(reverse('current_visit'))
        self.assertContains(current, other_table.name_ar)
        self.assertNotContains(current, self.table.name_ar)

    def test_table_qr_never_shares_visit_between_browsers(self):
        self.enable()
        first = self.client.post(self.url, self.payload())
        visit_a = Order.objects.get().visit
        other = Client()
        page = other.get(self.url)
        self.assertNotContains(page, 'جلستك')
        other.post(self.url, self.payload())
        self.assertEqual(HubVisit.objects.count(), 2)
        self.assertNotEqual(Order.objects.exclude(visit=visit_a).get().visit, visit_a)
        self.client.cookies['hub_visit'] = first.cookies['hub_visit'].value

    def test_invalid_and_closed_credentials_cannot_access_dashboard(self):
        self.enable()
        self.client.cookies['hub_visit'] = 'invalid-token'
        self.assertRedirects(self.client.get(reverse('current_visit')), reverse('menu_public'))
        response = self.client.post(self.url, self.payload())
        raw = response.cookies['hub_visit'].value
        visit = HubVisit.objects.get()
        visit.status = HubVisit.Status.CLOSED; visit.save(update_fields=['status'])
        self.client.cookies['hub_visit'] = raw
        self.assertRedirects(self.client.get(reverse('current_visit')), reverse('menu_public'))


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
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
        blocked = self.client.post(reverse('staff_visit_detail', kwargs={'public_code': visit.public_code}), {'action': 'close'})
        visit.refresh_from_db(); self.assertEqual(visit.status, 'open')
        Payment.objects.create(order=order, amount_syp=500, method=Payment.Method.CASH, created_by=self.user)
        self.assertEqual(visit.paid_syp, 500); self.assertEqual(visit.remaining_syp, 0)
        self.client.post(reverse('staff_visit_detail', kwargs={'public_code': visit.public_code}), {'action': 'close'})
        visit.refresh_from_db(); self.assertEqual(visit.status, 'closed')
        self.assertTrue(ActivityLog.objects.filter(action='visit.closed').exists())

    def test_standalone_order_remains_valid(self):
        order = Order.objects.create()
        self.assertIsNone(order.visit)
