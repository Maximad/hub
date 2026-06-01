from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from catalog.models import ProductOption, ProductOptionGroup, ProductOptionGroupAssignment
from core.models import Category, Order, OrderItem, Product


class SeedMasharibMenuCommandTests(TestCase):
    def call_seed(self, *args):
        out = StringIO()
        call_command('seed_masharib_menu', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_rolls_back_all_changes(self):
        self.call_seed('--dry-run', '--delete-old')

        self.assertFalse(Product.objects.filter(name_ar='قطعة كيش اليوم').exists())
        self.assertFalse(Category.objects.filter(name_ar='الكيش').exists())
        self.assertFalse(ProductOptionGroup.objects.filter(code='masharib_drink_upgrade').exists())

    def test_seed_is_idempotent_for_products_categories_and_options(self):
        self.call_seed('--delete-old')
        first_counts = {
            'categories': Category.objects.count(),
            'products': Product.objects.count(),
            'option_groups': ProductOptionGroup.objects.filter(code__startswith='masharib_').count(),
            'options': ProductOption.objects.filter(group__code__startswith='masharib_').count(),
            'assignments': ProductOptionGroupAssignment.objects.filter(group__code__startswith='masharib_').count(),
        }

        self.call_seed('--delete-old')

        self.assertEqual(first_counts['categories'], Category.objects.count())
        self.assertEqual(first_counts['products'], Product.objects.count())
        self.assertEqual(first_counts['option_groups'], ProductOptionGroup.objects.filter(code__startswith='masharib_').count())
        self.assertEqual(first_counts['options'], ProductOption.objects.filter(group__code__startswith='masharib_').count())
        self.assertEqual(first_counts['assignments'], ProductOptionGroupAssignment.objects.filter(group__code__startswith='masharib_').count())
        self.assertEqual(Product.objects.filter(name_ar='كومبو الصباح').count(), 1)
        self.assertEqual(Product.objects.get(name_ar='كومبو الصباح').option_group_assignments.filter(is_active=True).count(), 1)

    def test_delete_old_deletes_unreferenced_and_deactivates_referenced_products(self):
        category = Category.objects.create(name_ar='قديم')
        unreferenced = Product.objects.create(category=category, name_ar='قديم غير مستخدم', price_syp=1)
        referenced = Product.objects.create(category=category, name_ar='قديم مستخدم', price_syp=2)
        order = Order.objects.create()
        OrderItem.objects.create(
            order=order,
            product=referenced,
            quantity=1,
            product_name_ar_snapshot=referenced.name_ar,
            unit_price_syp_snapshot=referenced.price_syp,
        )

        self.call_seed('--delete-old')

        self.assertFalse(Product.objects.filter(pk=unreferenced.pk).exists())
        referenced.refresh_from_db()
        self.assertFalse(referenced.is_available)
        self.assertFalse(referenced.visible_on_qr)
        self.assertFalse(referenced.orderable_on_qr)
        self.assertTrue(referenced.metadata['archived_by_seed_masharib_menu'])
