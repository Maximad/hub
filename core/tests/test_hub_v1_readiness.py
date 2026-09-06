import json
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from catalog.models import MenuSection, ProductOption, ProductOptionGroup, ProductOptionGroupAssignment
from core.models import Category, Product


class HubV1ReadinessTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name_ar='طعام')
        self.section = MenuSection.objects.create(name_ar='المنيو', is_active=True, visible_on_qr=True)
        self.product = Product.objects.create(
            category=self.category,
            name_ar='ساندويشة',
            price_syp=100,
            is_available=True,
            visible_on_qr=True,
            orderable_on_qr=True,
            item_type=Product.ItemType.FOOD,
            product_type=Product.ProductType.FOOD,
        )
        self.product.menu_sections.add(self.section)

    def run_json(self):
        out = StringIO()
        call_command('hub_v1_readiness', '--json', stdout=out)
        return json.loads(out.getvalue())

    def test_valid_option_assignment_is_not_a_launch_failure(self):
        group = ProductOptionGroup.objects.create(
            code='valid-launch-options',
            name_ar='إضافات',
            is_active=True,
        )
        ProductOption.objects.create(
            group=group,
            code='cheese',
            name_ar='جبنة',
            is_active=True,
        )
        ProductOptionGroupAssignment.objects.create(product=self.product, group=group, is_active=True)

        payload = self.run_json()
        option_check = next(row for row in payload['checks'] if row['code'] == 'invalid_option_assignments')
        empty_check = next(row for row in payload['checks'] if row['code'] == 'empty_option_assignments')
        self.assertEqual(option_check['status'], 'PASS')
        self.assertEqual(empty_check['status'], 'PASS')

    def test_active_assignment_without_active_options_blocks_readiness(self):
        group = ProductOptionGroup.objects.create(
            code='empty-launch-options',
            name_ar='إضافات فارغة',
            is_active=True,
        )
        ProductOption.objects.create(
            group=group,
            code='old',
            name_ar='قديم',
            is_active=False,
        )
        ProductOptionGroupAssignment.objects.create(product=self.product, group=group, is_active=True)

        with self.assertRaises(CommandError):
            call_command('hub_v1_readiness', stdout=StringIO())

    def test_legacy_pos_flag_mismatch_is_not_a_readiness_warning(self):
        # Reproduce old imported data without invoking the save-time synchronizer.
        Product.objects.filter(pk=self.product.pk).update(
            visible_on_pos=False,
            orderable_on_pos=False,
        )
        payload = self.run_json()
        check = next(row for row in payload['checks'] if row['code'] == 'catalog_visibility_source')
        self.assertEqual(check['status'], 'PASS')
        self.assertNotIn(
            'legacy_pos_only_visibility',
            [row['code'] for row in payload['checks']],
        )
