from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.models import (
    MenuSection,
    ProductOption,
    ProductOptionGroup,
    ProductOptionGroupAssignment,
)
from core.models import Category, Product


class PosCatalogConsistencyTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='pos-catalog-admin',
            password='pass',
            phone='+963000008001',
            role='admin',
            is_staff=True,
        )
        self.client.force_login(self.admin)
        self.category = Category.objects.create(name_ar='سندويشات')
        self.visible_section = MenuSection.objects.create(
            name_ar='سندويشات هَب',
            is_active=True,
            visible_on_qr=True,
        )
        self.hidden_section = MenuSection.objects.create(
            name_ar='قسم مخفي',
            is_active=True,
            visible_on_qr=False,
        )

    def product(self, name, *, visible_on_qr=True, orderable_on_qr=True, section=None):
        product = Product.objects.create(
            category=self.category,
            name_ar=name,
            price_syp=100,
            is_available=True,
            visible_on_qr=visible_on_qr,
            orderable_on_qr=orderable_on_qr,
            item_type=Product.ItemType.FOOD,
            product_type=Product.ProductType.FOOD,
        )
        if section is not None:
            product.menu_sections.add(section)
        return product

    def test_pos_uses_public_section_and_product_visibility_as_baseline(self):
        visible = self.product('ظاهر', section=self.visible_section)
        self.product('منتج مخفي', visible_on_qr=False, section=self.visible_section)
        self.product('داخل قسم مخفي', section=self.hidden_section)
        self.product('بدون قسم')

        response = self.client.get(reverse('staff_pos'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn(visible.name_ar, content)
        self.assertNotIn('منتج مخفي', content)
        self.assertNotIn('داخل قسم مخفي', content)
        self.assertNotIn('قسم مخفي', content)
        self.assertNotIn('بدون قسم', content)

    def test_pos_uses_canonical_orderable_field(self):
        visible = self.product('قابل للطلب', section=self.visible_section)
        self.product('غير قابل للطلب', orderable_on_qr=False, section=self.visible_section)

        response = self.client.get(reverse('staff_pos'))
        content = response.content.decode('utf-8')
        self.assertIn(visible.name_ar, content)
        self.assertNotIn('غير قابل للطلب', content)

    def test_stale_legacy_pos_flags_do_not_hide_canonical_product(self):
        product = self.product('حقول قديمة متعارضة', section=self.visible_section)
        # Bypass Product.save/pre_save to reproduce historical production rows.
        Product.objects.filter(pk=product.pk).update(
            visible_on_pos=False,
            orderable_on_pos=False,
        )

        response = self.client.get(reverse('staff_pos'))
        self.assertContains(response, product.name_ar)

    def test_internet_identity_stays_out_of_generic_pos(self):
        product = Product.objects.create(
            category=self.category,
            name_ar='باقة إنترنت داخلية',
            price_syp=100,
            is_available=True,
            visible_on_qr=True,
            orderable_on_qr=True,
            item_type=Product.ItemType.SERVICE,
            service_type=Product.ServiceType.INTERNET,
            product_type=Product.ProductType.INTERNET,
        )
        product.menu_sections.add(self.visible_section)

        response = self.client.get(reverse('staff_pos'))
        self.assertNotContains(response, product.name_ar)

    def test_active_modifier_configuration_appears_in_pos(self):
        sandwich = self.product('ساندويشة اختبار', section=self.visible_section)
        group = ProductOptionGroup.objects.create(
            code='launch-sandwich-extras',
            name_ar='إضافات السندويشة',
            selection_type=ProductOptionGroup.SelectionType.MULTIPLE,
            is_active=True,
        )
        visible_option = ProductOption.objects.create(
            group=group,
            code='extra-cheese',
            name_ar='جبنة إضافية',
            price_delta_syp=25,
            is_active=True,
        )
        ProductOption.objects.create(
            group=group,
            code='hidden-old-option',
            name_ar='خيار قديم مخفي',
            is_active=False,
        )
        ProductOptionGroupAssignment.objects.create(
            product=sandwich,
            group=group,
            is_active=True,
        )

        response = self.client.get(reverse('staff_pos'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn(group.name_ar, content)
        self.assertIn(visible_option.name_ar, content)
        self.assertNotIn('خيار قديم مخفي', content)

    def test_invalid_modifier_applicability_does_not_silently_render(self):
        sandwich = self.product('ساندويشة تصنيف', section=self.visible_section)
        group = ProductOptionGroup.objects.create(
            code='beverage-only-launch',
            name_ar='خيارات مشروبات فقط',
            selection_type=ProductOptionGroup.SelectionType.SINGLE,
            applies_to_item_type='beverage',
            is_active=True,
        )
        ProductOption.objects.create(
            group=group,
            code='ice',
            name_ar='ثلج',
            is_active=True,
        )
        # Deliberately bypass full_clean to reproduce legacy/imported bad data.
        ProductOptionGroupAssignment.objects.create(
            product=sandwich,
            group=group,
            is_active=True,
        )

        response = self.client.get(reverse('staff_pos'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(group.name_ar, response.content.decode('utf-8'))
