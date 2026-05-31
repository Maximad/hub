from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import ProtectedError

from catalog.models import MenuSection, PrepStation, ProductOption, ProductOptionGroup, ProductOptionGroupAssignment
from core.models import Category, Product


@dataclass(frozen=True)
class OptionDef:
    code: str
    name_ar: str
    price_delta_syp: int = 0


@dataclass(frozen=True)
class ProductDef:
    code: str
    category: str
    name_ar: str
    price_syp: int
    description_ar: str = ''
    available: bool = True
    event_only: bool = False
    option_group_code: str | None = None
    item_type: str = Product.ItemType.FOOD
    beverage_type: str = ''
    food_type: str = Product.FoodType.HUB_FOOD
    is_alcoholic: bool = False


@dataclass(frozen=True)
class OptionGroupDef:
    code: str
    name_ar: str
    applies_to_item_type: str
    options: tuple[OptionDef, ...]


CATEGORY_NAMES = (
    'الكيش',
    'كومبو الصباح',
    'المازة',
    'مشروبات ساخنة',
    'مشروبات باردة',
    'مشروبات توقيع',
    'صواني وطلبات طاولة',
    'مناسبات فقط',
)

OPTION_GROUPS = {
    'drink_upgrade': OptionGroupDef(
        code='masharib_drink_upgrade',
        name_ar='ترقية المشروب',
        applies_to_item_type=Product.ItemType.FOOD,
        options=(
            OptionDef('regular_tea', 'شاي عادي', 0),
            OptionDef('coffee', 'قهوة', 0),
            OptionDef('canned_drink', 'مشروب معلب', 0),
            OptionDef('mint_or_sage_tea', 'شاي نعنع أو ميرمية', 1000),
            OptionDef('zhourat', 'زهورات', 3000),
            OptionDef('hot_chocolate', 'هوت شوكليت', 4000),
            OptionDef('dibs_lemon', 'دبس وليمون', 6000),
            OptionDef('matcha', 'ماتشا', 10000),
            OptionDef('iced_matcha_latte', 'آيس ماتشا لاتيه', 16000),
        ),
    ),
    'mezza_type': OptionGroupDef(
        code='masharib_mezza_type',
        name_ar='نوع المازا',
        applies_to_item_type=Product.ItemType.FOOD,
        options=(
            OptionDef('herb_potatoes', 'بطاطا بالأعشاب', 0),
            OptionDef('herb_yogurt', 'لبن بالأعشاب', 0),
            OptionDef('mutabbal_eggplant', 'متبّل باذنجان', 0),
            OptionDef('cabbage_yogurt_salad', 'سلطة ملفوف ولبن', 0),
            OptionDef('pickles_vegetables', 'مخللات وخضار', 0),
            OptionDef('tomato_mint_salad', 'سلطة بندورة ونعنع', 0),
            OptionDef('radish_lemon_sumac', 'فجل وليمون وسماق', 0),
            OptionDef('tahini_lemon', 'طحينية وليمون', 0),
            OptionDef('mild_spicy_potatoes', 'بطاطا حارة خفيفة', 0),
            OptionDef('pomegranate_molasses_eggplant', 'باذنجان بدبس رمان', 0),
        ),
    ),
    'sugar': OptionGroupDef(
        code='masharib_sugar',
        name_ar='السكر',
        applies_to_item_type=Product.ItemType.BEVERAGE,
        options=(
            OptionDef('no_sugar', 'بدون سكر', 0),
            OptionDef('light_sugar', 'سكر خفيف', 0),
            OptionDef('normal', 'عادي', 0),
            OptionDef('extra_sugar', 'زيادة سكر', 0),
        ),
    ),
    'serving': OptionGroupDef(
        code='masharib_serving',
        name_ar='التقديم',
        applies_to_item_type=Product.ItemType.BEVERAGE,
        options=(
            OptionDef('with_ice', 'مع ثلج', 0),
            OptionDef('no_ice', 'بدون ثلج', 0),
            OptionDef('lemon', 'ليمون', 1000),
        ),
    ),
    'additions': OptionGroupDef(
        code='masharib_additions',
        name_ar='إضافات',
        applies_to_item_type=Product.ItemType.BEVERAGE,
        options=(
            OptionDef('no_additions', 'بدون إضافات', 0),
            OptionDef('nuts', 'مكسرات', 3000),
        ),
    ),
}

PRODUCTS = (
    ProductDef('daily_quiche_slice', 'الكيش', 'قطعة كيش اليوم', 20000, 'نكهة اليوم. لا توجد تعديلات داخل القطعة بعد الطبخ.'),
    ProductDef('whole_quiche_tray', 'الكيش', 'صينية كيش كاملة', 130000, 'طلب مسبق. 6–8 قطع تقريباً. السعر قابل للتعديل حسب نكهة اليوم.'),
    ProductDef('morning_combo', 'كومبو الصباح', 'كومبو الصباح', 50000, 'قطعة كيش اليوم + 3 مازات صغيرة + شاي عادي أو قهوة أو مشروب معلب.', option_group_code='drink_upgrade'),
    ProductDef('small_mezza', 'المازة', 'مازا صغيرة', 8000, 'حجم صغير مناسب كإضافة جانبية أو ضمن الكومبو.', option_group_code='mezza_type'),
    ProductDef('single_mezza', 'المازة', 'مازا مفردة', 10000, 'مازا واحدة بحجم عادي.', option_group_code='mezza_type'),
    ProductDef('three_mezza_plate', 'المازة', 'طبق 3 مازات', 25000, 'ثلاث مازات صغيرة مختارة.'),
    ProductDef('five_mezza_plate', 'المازة', 'طبق 5 مازات', 40000, 'طبق مشاركة للطاولة.'),
    ProductDef('regular_tea', 'مشروبات ساخنة', 'شاي عادي', 4000, option_group_code='sugar', item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.TEA, food_type=''),
    ProductDef('mint_tea', 'مشروبات ساخنة', 'شاي مع نعنع', 5000, item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.TEA, food_type=''),
    ProductDef('sage_tea', 'مشروبات ساخنة', 'شاي مع ميرمية', 5000, item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.TEA, food_type=''),
    ProductDef('earl_grey_tea', 'مشروبات ساخنة', 'شاي إيرل غراي', 6000, item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.TEA, food_type=''),
    ProductDef('fruit_flavored_tea', 'مشروبات ساخنة', 'شاي فواكه / منكه', 7000, item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.TEA, food_type=''),
    ProductDef('zhourat', 'مشروبات ساخنة', 'زهورات', 8000, item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.ZHOURAT, food_type=''),
    ProductDef('hot_chocolate_water', 'مشروبات ساخنة', 'هوت شوكليت بالماء', 8000, item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.OTHER, food_type=''),
    ProductDef('hot_chocolate_milk', 'مشروبات ساخنة', 'هوت شوكليت بالحليب', 14000, item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.OTHER, food_type=''),
    ProductDef('matcha_water', 'مشروبات ساخنة', 'ماتشا بالماء', 14000, item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.TEA, food_type=''),
    ProductDef('hot_matcha_latte', 'مشروبات ساخنة', 'ماتشا لاتيه ساخن', 20000, item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.TEA, food_type=''),
    ProductDef('cola_7up_glass', 'مشروبات باردة', 'كولا / سفن أب — كاسة', 6000, option_group_code='serving', item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.OTHER, food_type=''),
    ProductDef('canned_fizzy_drink', 'مشروبات باردة', 'مشروب معلب / غازي', 9000, 'السعر الوسطي. يمكن تعديله حسب النوع.', item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.OTHER, food_type=''),
    ProductDef('juice_glass', 'مشروبات باردة', 'عصير كاسة', 8000, item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.JUICE, food_type=''),
    ProductDef('large_juice', 'مشروبات باردة', 'عصير كبير', 12000, item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.JUICE, food_type=''),
    ProductDef('cold_yogurt_drink', 'مشروبات باردة', 'شنينة / لبن بارد', 10000, item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.LOCAL, food_type=''),
    ProductDef('dibs_lemon', 'مشروبات باردة', 'دبس وليمون', 12000, item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.LOCAL, food_type=''),
    ProductDef('karma_jallab', 'مشروبات باردة', 'جلاب الكرمة', 15000, option_group_code='additions', item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.LOCAL, food_type=''),
    ProductDef('iced_matcha_latte', 'مشروبات باردة', 'آيس ماتشا لاتيه', 24000, item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.TEA, food_type=''),
    ProductDef('matcha_special', 'مشروبات باردة', 'ماتشا سبيشال', 28000, item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.TEA, food_type=''),
    ProductDef('wajib_cup_mate', 'مشروبات توقيع', 'كاسة الواجب — متّة مشاريب', 12000, 'متّة مشاريب. عادي / نعنع / ليمون.', item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.MATE, food_type=''),
    ProductDef('table_mate', 'مشروبات توقيع', 'متّة طاولة', 25000, 'لشخصين أو أكثر.', item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.MATE, food_type=''),
    ProductDef('basalt_zhourat', 'مشروبات توقيع', 'زهرات البازلت', 8000, 'ساخنة أو باردة حسب الموسم.', item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.ZHOURAT, food_type=''),
    ProductDef('apple_sage_tea', 'مشروبات توقيع', 'شاي تفاح وميرمية', 10000, item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.TEA, food_type=''),
    ProductDef('quiche_five_mezza_tray', 'صواني وطلبات طاولة', 'صينية كيش + طبق 5 مازات', 160000),
    ProductDef('quiche_seven_mezza_tray', 'صواني وطلبات طاولة', 'صينية كيش + طبق 7 مازات', 175000),
    ProductDef('large_seven_mezza_plate', 'صواني وطلبات طاولة', 'طبق 7 مازات كبير', 55000),
    ProductDef('rayan_arak_glass', 'مناسبات فقط', 'عرق الريان — كأس', 12000, available=False, event_only=True, item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.ARAK, food_type='', is_alcoholic=True),
    ProductDef('rayan_arak_small_carafe', 'مناسبات فقط', 'عرق الريان — كاراف صغير', 45000, available=False, event_only=True, item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.ARAK, food_type='', is_alcoholic=True),
    ProductDef('rayan_arak_table_liter', 'مناسبات فقط', 'عرق الريان — لتر طاولة', 120000, available=False, event_only=True, item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.ARAK, food_type='', is_alcoholic=True),
    ProductDef('light_beer', 'مناسبات فقط', 'بيرة خفيفة', 45000, available=False, event_only=True, item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.BEER, food_type='', is_alcoholic=True),
    ProductDef('strong_beer', 'مناسبات فقط', 'بيرة ثقيلة', 50000, available=False, event_only=True, item_type=Product.ItemType.BEVERAGE, beverage_type=Product.BeverageType.BEER, food_type='', is_alcoholic=True),
    ProductDef('arak_glass_two_mezza', 'مناسبات فقط', 'كأس عرق + مازتين', 32000, available=False, event_only=True, is_alcoholic=True),
    ProductDef('small_arak_carafe_three_mezza', 'مناسبات فقط', 'كاراف عرق صغير + 3 مازات', 70000, available=False, event_only=True, is_alcoholic=True),
    ProductDef('arak_liter_four_mezza', 'مناسبات فقط', 'لتر عرق + طبق 4 مازات', 155000, available=False, event_only=True, is_alcoholic=True),
)


class Command(BaseCommand):
    help = 'Seed the current Masharib public/event menu safely and idempotently.'

    def add_arguments(self, parser):
        parser.add_argument('--delete-old', action='store_true', help='Delete unreferenced old products and deactivate referenced old products.')
        parser.add_argument('--dry-run', action='store_true', help='Show the planned changes and roll the transaction back.')

    def handle(self, *args: Any, **options: Any):
        dry_run = options['dry_run']
        delete_old = options['delete_old']
        summary = {
            'categories_created': 0,
            'categories_updated': 0,
            'sections_created': 0,
            'sections_updated': 0,
            'products_created': 0,
            'products_updated': 0,
            'old_products_deleted': 0,
            'old_products_deactivated': 0,
            'duplicate_products_removed': 0,
            'duplicate_products_deactivated': 0,
            'option_groups_created': 0,
            'option_groups_updated': 0,
            'options_created': 0,
            'options_updated': 0,
            'assignments_created': 0,
            'assignments_updated': 0,
        }

        with transaction.atomic():
            stations = self._ensure_prep_stations()
            categories, sections = self._seed_categories(summary)
            option_groups = self._seed_option_groups(summary)
            seeded_product_ids = self._seed_products(categories, sections, stations, option_groups, summary)
            self._remove_duplicate_seed_products(seeded_product_ids, summary)
            if delete_old:
                self._delete_or_deactivate_old_products(seeded_product_ids, summary)
            if dry_run:
                transaction.set_rollback(True)

        prefix = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(f'{prefix}Masharib menu seed complete.'))
        for key, value in summary.items():
            self.stdout.write(f'{key}: {value}')
        if dry_run:
            self.stdout.write(self.style.WARNING('Dry run only: all database changes were rolled back.'))
        elif not delete_old:
            self.stdout.write(self.style.WARNING('Old products were left unchanged. Re-run with --delete-old to clean them up safely.'))

    def _ensure_prep_stations(self):
        stations = {}
        for code, name_ar in [('bar', 'بار مشاريب'), ('kitchen', 'مطبخ مشاريب')]:
            station, _ = PrepStation.objects.update_or_create(code=code, defaults={'name_ar': name_ar, 'is_active': True})
            stations[code] = station
        return stations

    def _seed_categories(self, summary):
        categories = {}
        sections = {}
        for sort_order, name_ar in enumerate(CATEGORY_NAMES, start=1):
            category, created = Category.objects.update_or_create(name_ar=name_ar, defaults={'description_ar': ''})
            summary['categories_created' if created else 'categories_updated'] += 1
            section, created = MenuSection.objects.update_or_create(
                name_ar=name_ar,
                defaults={'sort_order': sort_order, 'is_active': True, 'visible_on_qr': name_ar != 'مناسبات فقط'},
            )
            summary['sections_created' if created else 'sections_updated'] += 1
            categories[name_ar] = category
            sections[name_ar] = section
        return categories, sections

    def _seed_option_groups(self, summary):
        option_groups = {}
        for sort_order, group_def in enumerate(OPTION_GROUPS.values(), start=1):
            group, created = ProductOptionGroup.objects.update_or_create(
                code=group_def.code,
                defaults={
                    'name_ar': group_def.name_ar,
                    'selection_type': ProductOptionGroup.SelectionType.SINGLE,
                    'is_required': False,
                    'min_selected': 0,
                    'max_selected': 1,
                    'applies_to_item_type': group_def.applies_to_item_type,
                    'applies_to_beverage_type': '',
                    'applies_to_food_type': '',
                    'applies_to_service_type': '',
                    'sort_order': sort_order,
                    'is_active': True,
                },
            )
            summary['option_groups_created' if created else 'option_groups_updated'] += 1
            option_groups[group_def.code] = group
            for option_order, option_def in enumerate(group_def.options, start=1):
                _, option_created = ProductOption.objects.update_or_create(
                    group=group,
                    code=option_def.code,
                    defaults={
                        'name_ar': option_def.name_ar,
                        'price_delta_syp': option_def.price_delta_syp,
                        'is_default': option_order == 1 and option_def.price_delta_syp == 0,
                        'is_active': True,
                        'sort_order': option_order,
                    },
                )
                summary['options_created' if option_created else 'options_updated'] += 1
        return option_groups

    def _seed_products(self, categories, sections, stations, option_groups, summary):
        seeded_ids = []
        for sort_order, product_def in enumerate(PRODUCTS, start=1):
            product = Product.objects.filter(metadata__masharib_menu_code=product_def.code).order_by('pk').first()
            created = False
            if product is None:
                product = Product.objects.filter(name_ar=product_def.name_ar).order_by('pk').first()
            if product is None:
                product = Product(name_ar=product_def.name_ar)
                created = True

            metadata = dict(product.metadata or {})
            metadata.update({'masharib_menu_code': product_def.code, 'event_only': product_def.event_only})
            product.category = categories[product_def.category]
            product.name_ar = product_def.name_ar
            product.description_ar = product_def.description_ar
            product.price_syp = product_def.price_syp
            product.is_available = product_def.available
            product.sort_order = sort_order
            product.item_type = product_def.item_type
            product.beverage_type = product_def.beverage_type
            product.food_type = product_def.food_type
            product.service_type = ''
            product.prep_station_ref = stations['bar'] if product_def.item_type == Product.ItemType.BEVERAGE else stations['kitchen']
            product.is_alcoholic = product_def.is_alcoholic
            product.age_restricted = product_def.is_alcoholic
            product.visible_on_qr = not product_def.event_only and product_def.available
            product.orderable_on_qr = not product_def.event_only and product_def.available
            product.requires_staff_confirmation = product_def.event_only or product_def.is_alcoholic
            product.available_for_events = True
            product.available_for_takeaway = False
            product.metadata = metadata
            product.save()
            product.menu_sections.set([sections[product_def.category]])
            seeded_ids.append(product.pk)
            summary['products_created' if created else 'products_updated'] += 1

            active_group_ids = []
            if product_def.option_group_code:
                group = option_groups[OPTION_GROUPS[product_def.option_group_code].code]
                assignment, assignment_created = ProductOptionGroupAssignment.objects.update_or_create(
                    product=product,
                    group=group,
                    defaults={'sort_order': 1, 'is_active': True},
                )
                active_group_ids.append(group.pk)
                summary['assignments_created' if assignment_created else 'assignments_updated'] += 1
            ProductOptionGroupAssignment.objects.filter(product=product).exclude(group_id__in=active_group_ids).update(is_active=False)
        return seeded_ids

    def _remove_duplicate_seed_products(self, seeded_product_ids, summary):
        seed_names = [product.name_ar for product in PRODUCTS]
        for name_ar in seed_names:
            matches = Product.objects.filter(name_ar=name_ar).exclude(pk__in=seeded_product_ids).order_by('pk')
            for duplicate in matches:
                if self._product_is_referenced(duplicate):
                    self._deactivate_product(duplicate)
                    summary['duplicate_products_deactivated'] += 1
                else:
                    duplicate.delete()
                    summary['duplicate_products_removed'] += 1

    def _delete_or_deactivate_old_products(self, seeded_product_ids, summary):
        old_products = Product.objects.exclude(pk__in=seeded_product_ids).order_by('pk')
        for product in old_products:
            if self._product_is_referenced(product):
                self._deactivate_product(product)
                summary['old_products_deactivated'] += 1
                continue
            try:
                product.delete()
                summary['old_products_deleted'] += 1
            except ProtectedError:
                self._deactivate_product(product)
                summary['old_products_deactivated'] += 1

    def _product_is_referenced(self, product):
        return product.order_items.exists()

    def _deactivate_product(self, product):
        metadata = dict(product.metadata or {})
        metadata.update({'archived_by_seed_masharib_menu': True})
        product.is_available = False
        product.visible_on_qr = False
        product.orderable_on_qr = False
        product.metadata = metadata
        product.save(update_fields=['is_available', 'visible_on_qr', 'orderable_on_qr', 'metadata', 'updated_at'])
