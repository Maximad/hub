from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalog.models import MediaAsset, MenuSection, ProductMedia
from core.models import Category, InventoryItem, Order, Payment, Product, SystemSetting
from events.models import Event
from members.models import MembershipPlan
from vendors.models import Vendor


@override_settings(DEBUG_PROPAGATE_EXCEPTIONS=True, ALLOWED_HOSTS=['testserver'])
class HubAdminDashboardTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.superuser = User.objects.create_superuser(username='admin-ui', password='pass', email='admin@example.com', phone='+963900000')
        self.manager = User.objects.create_user(username='manager-ui', password='pass', email='manager@example.com', phone='+963900001', is_staff=True, role='admin')
        self.manager.user_permissions.add(*Permission.objects.filter(content_type__app_label='core', content_type__model__in=['order', 'payment']))
        self.category = Category.objects.create(name_ar='مشروبات')
        self.product = Product.objects.create(category=self.category, name_ar='قهوة عربية', name_en='Arabic Coffee', price_syp=12000)
        self.asset = MediaAsset.objects.create(title_ar='صورة القهوة', media_type=MediaAsset.MediaType.EXTERNAL_URL, external_url='https://example.com/coffee.jpg')
        ProductMedia.objects.create(product=self.product, media_asset=self.asset, is_primary=True)
        self.order = Order.objects.create()
        self.payment = Payment.objects.create(order=self.order, amount_syp=0, method=Payment.Method.UNPAID)

    def test_admin_index_loads_for_superuser_with_arabic_sections_and_quick_links(self):
        self.client.force_login(self.superuser)
        response = self.client.get('/admin/')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        for text in ['المنتجات والمنيو', 'التشغيل والمبيعات', 'المالية والمخزون', 'الأعضاء والإنترنت', 'التشغيل اليومي', 'فتح نقطة البيع', '/staff/cashier/', 'إعدادات النظام']:
            self.assertIn(text, content)

    def test_hidden_technical_models_do_not_appear_but_direct_url_remains_accessible(self):
        self.client.force_login(self.superuser)
        index = self.client.get('/admin/').content.decode()
        self.assertNotIn('سجل رصيد الأعضاء', index)
        self.assertNotIn('وسائط المنتجات</a>', index)
        response = self.client.get(reverse('admin:catalog_productmedia_changelist'))
        self.assertEqual(response.status_code, 200)

    def test_product_inline_media_and_admin_pages_load(self):
        self.client.force_login(self.superuser)
        product_change = self.client.get(reverse('admin:core_product_change', args=[self.product.pk]))
        self.assertEqual(product_change.status_code, 200)
        self.assertContains(product_change, 'ربط المنتج بوسائط من مكتبة الوسائط')
        for name in ['admin:core_product_changelist', 'admin:catalog_mediaasset_changelist', 'admin:core_systemsetting_changelist']:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)


    def test_product_changelist_restores_clickable_arabic_name(self):
        self.client.force_login(self.superuser)
        url = reverse('admin:core_product_changelist')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        change_url = reverse('admin:core_product_change', args=[self.product.pk])
        self.assertContains(response, f'href="{change_url}"', html=False)
        self.assertContains(response, 'قهوة عربية')

    def test_product_admin_identifying_link_configuration(self):
        product_admin = admin.site._registry[Product]
        self.assertEqual(product_admin.list_display_links, ('name_ar',))
        self.assertIn('name_ar', product_admin.list_display)
        self.assertNotIn('name_ar', product_admin.list_editable)

    def test_admin_pages_are_rtl_and_custom_css_is_loaded(self):
        self.client.force_login(self.superuser)
        for name in ['admin:index', 'admin:core_product_changelist', 'admin:core_product_change']:
            with self.subTest(name=name):
                args = [self.product.pk] if name.endswith('_change') else None
                response = self.client.get(reverse(name, args=args))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, '<html lang="ar" dir="rtl">', html=False)
                self.assertContains(response, 'admin/css/hub_admin.css')

    def test_major_admin_changelists_keep_identifying_links(self):
        MenuSection.objects.create(name_ar='ساخن')
        InventoryItem.objects.create(name_ar='بن', unit='g')
        MembershipPlan.objects.create(name_ar='ذهبي', code='gold', price_syp=1000)
        Event.objects.create(title_ar='أمسية موسيقية', starts_at=timezone.now())
        Vendor.objects.create(name_ar='مخبز الحارة')
        self.client.force_login(self.superuser)
        checks = [
            ('admin:core_category_changelist', 'admin:core_category_change', self.category.pk, 'مشروبات'),
            ('admin:catalog_menusection_changelist', 'admin:catalog_menusection_change', MenuSection.objects.get(name_ar='ساخن').pk, 'ساخن'),
            ('admin:catalog_mediaasset_changelist', 'admin:catalog_mediaasset_change', self.asset.pk, 'صورة القهوة'),
            ('admin:core_member_changelist', None, None, None),
            ('admin:core_inventoryitem_changelist', 'admin:core_inventoryitem_change', InventoryItem.objects.get(name_ar='بن').pk, 'بن'),
            ('admin:members_membershipplan_changelist', 'admin:members_membershipplan_change', MembershipPlan.objects.get(code='gold').pk, 'ذهبي'),
            ('admin:events_event_changelist', 'admin:events_event_change', Event.objects.get(title_ar='أمسية موسيقية').pk, 'أمسية موسيقية'),
            ('admin:vendors_vendor_changelist', 'admin:vendors_vendor_change', Vendor.objects.get(name_ar='مخبز الحارة').pk, 'مخبز الحارة'),
        ]
        for changelist_name, change_name, pk, label in checks:
            with self.subTest(changelist_name=changelist_name):
                response = self.client.get(reverse(changelist_name))
                self.assertEqual(response.status_code, 200)
                if change_name:
                    self.assertContains(response, f'href="{reverse(change_name, args=[pk])}"', html=False)
                    self.assertContains(response, label)

    def test_product_change_page_remains_accessible(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('admin:core_product_change', args=[self.product.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'قهوة عربية')

    def test_risky_models_are_readonly_for_non_superuser_and_superuser_can_change(self):
        factory = RequestFactory()
        request = factory.get('/admin/core/payment/')
        request.user = self.manager
        payment_admin = admin.site._registry[Payment]
        readonly = payment_admin.get_readonly_fields(request, self.payment)
        self.assertIn('amount_syp', readonly)
        self.assertFalse(payment_admin.has_delete_permission(request, self.payment))
        super_request = factory.get('/admin/core/payment/')
        super_request.user = self.superuser
        self.assertNotIn('amount_syp', payment_admin.get_readonly_fields(super_request, self.payment))
        self.assertTrue(payment_admin.has_delete_permission(super_request, self.payment))

    def test_staff_and_public_routes_still_resolve(self):
        for route in ['/staff/', '/menu/']:
            with self.subTest(route=route):
                self.assertLess(self.client.get(route).status_code, 500)
