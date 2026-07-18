from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.management.commands.import_inventory_items import _decimal, _text, _xlsx_rows
from core.models import InventoryItem, Product, ProductRecipeItem

SHEET_NAME = 'وصفات المنتجات'

HEADERS = [
    'حالة الاستيراد',
    'اسم المنتج',
    'مفتاح المنتج',
    'مادة المخزون',
    'كود مادة المخزون',
    'الكمية المدخلة',
    'وحدة الإدخال',
    'وحدة المخزون',
    'الكمية الموحّدة',
    'نسبة الهدر %',
    'نشط',
    'كلفة وحدة المخزون',
    'كلفة السطر',
    'ملاحظات',
    'التحقق',
]

READY = 'جاهز للاستيراد'
REVIEW = 'بحاجة مراجعة'
VALID = 'صالح'
ALWAYS_SKIP = {'مسودة', 'تجاهل'}
UNIT_LABELS = {
    label: value for value, label in InventoryItem.Unit.choices
} | {
    value: value for value, _label in InventoryItem.Unit.choices
}

CONVERSIONS = {
    ('g', 'kg'): Decimal('0.001'),
    ('kg', 'g'): Decimal('1000'),
    ('ml', 'liter'): Decimal('0.001'),
    ('liter', 'ml'): Decimal('1000'),
}
TOLERANCE = Decimal('0.0005')


class RowImportError(Exception):
    category = 'failed'


class MissingProduct(RowImportError):
    category = 'missing_product'


class AmbiguousProduct(RowImportError):
    category = 'ambiguous_product'


class MissingInventory(RowImportError):
    category = 'missing_inventory'


class InvalidUnit(RowImportError):
    category = 'invalid_unit'


def read_recipe_rows(path, sheet_name=SHEET_NAME):
    rows = iter(_xlsx_rows(path, sheet_name))
    try:
        headers = [_text(header) for header in next(rows)]
    except StopIteration as exc:
        raise CommandError(f'Sheet is empty: {sheet_name}') from exc
    for missing in set(HEADERS) - set(headers):
        raise CommandError(f'Missing required column: {missing}')
    for excel_row_number, values in enumerate(rows, start=2):
        record = {headers[index]: values[index] if index < len(values) else None for index in range(len(headers))}
        if not any(_text(value) for value in record.values()):
            continue
        yield excel_row_number, record


def resolve_inventory(record):
    code = _text(record['كود مادة المخزون'])
    if not code:
        raise MissingInventory('Inventory code is required')
    matches = list(InventoryItem.objects.filter(code=code)[:2])
    if len(matches) != 1:
        raise MissingInventory(f'InventoryItem with code {code} was not found')
    return matches[0]


def resolve_product(record):
    product_key = _text(record['مفتاح المنتج'])
    product_name = _text(record['اسم المنتج'])
    if product_key:
        qs = Product.objects.filter(metadata__masharib_menu_code=product_key)
        label = f'masharib_menu_code {product_key}'
    else:
        if not product_name:
            raise MissingProduct('Product name is required when product key is blank')
        qs = Product.objects.filter(name_ar=product_name)
        label = f'name_ar {product_name}'
    matches = list(qs[:2])
    if not matches:
        raise MissingProduct(f'Product with {label} was not found')
    if len(matches) > 1:
        raise AmbiguousProduct(f'Multiple products match {label}')
    return matches[0]


def parse_unit(value):
    label = _text(value)
    if label not in UNIT_LABELS:
        raise InvalidUnit(f'Unknown input unit: {label}')
    return UNIT_LABELS[label]


def normalize_quantity(record, inventory_item):
    quantity = _decimal(record['الكمية المدخلة'])
    if quantity is None:
        raise RowImportError('Input quantity is required')
    input_unit = parse_unit(record['وحدة الإدخال'])
    stock_unit = inventory_item.unit
    if input_unit == stock_unit:
        normalized = quantity
    elif (input_unit, stock_unit) in CONVERSIONS:
        normalized = quantity * CONVERSIONS[(input_unit, stock_unit)]
    else:
        raise InvalidUnit(f'Unsupported unit conversion: {input_unit} -> {stock_unit}')

    sheet_normalized = _decimal(record['الكمية الموحّدة'])
    if sheet_normalized is not None and abs(sheet_normalized - normalized) > TOLERANCE:
        raise InvalidUnit(f'Normalized quantity mismatch: calculated {normalized}, spreadsheet {sheet_normalized}')
    return normalized


def parse_active(value):
    label = _text(value)
    if label == 'نعم':
        return True
    if label == 'لا':
        return False
    raise RowImportError(f'Invalid active value: {label}')


def build_recipe(record):
    inventory_item = resolve_inventory(record)
    product = resolve_product(record)
    normalized_quantity = normalize_quantity(record, inventory_item)
    waste_percent = _decimal(record['نسبة الهدر %'], default_zero=True)
    active = parse_active(record['نشط'])
    notes = _text(record['ملاحظات'])
    defaults = {
        'quantity_per_unit': normalized_quantity,
        'waste_factor_percent': waste_percent,
        'is_active': active,
        'notes': notes,
    }
    return product, inventory_item, defaults


class Command(BaseCommand):
    help = 'Import product recipe items from the Hub Sweida inventory workbook.'

    def add_arguments(self, parser):
        parser.add_argument('workbook', help='Path to the .xlsx recipe workbook')
        parser.add_argument('--include-review', action='store_true', help='Also import rows marked بحاجة مراجعة')
        parser.add_argument('--dry-run', action='store_true', help='Validate and report changes without saving them')

    def handle(self, *args, **options):
        workbook = Path(options['workbook'])
        if not workbook.exists():
            raise CommandError(f'Workbook does not exist: {workbook}')

        dry_run = options['dry_run']
        allowed_statuses = {READY} | ({REVIEW} if options['include_review'] else set())
        totals = {
            'created': 0,
            'updated': 0,
            'skipped_status': 0,
            'missing_product': 0,
            'ambiguous_product': 0,
            'missing_inventory': 0,
            'invalid_unit': 0,
            'failed': 0,
        }
        with transaction.atomic():
            for row_number, record in read_recipe_rows(workbook):
                status = _text(record['حالة الاستيراد'])
                verification = _text(record['التحقق'])
                if status in ALWAYS_SKIP or status not in allowed_statuses or verification != VALID:
                    totals['skipped_status'] += 1
                    continue
                try:
                    product, inventory_item, defaults = build_recipe(record)
                    existing = ProductRecipeItem.objects.filter(
                        product=product,
                        inventory_item=inventory_item,
                        unit=inventory_item.unit,
                    ).first()
                    candidate = existing or ProductRecipeItem(product=product, inventory_item=inventory_item, unit=inventory_item.unit)
                    for field, value in defaults.items():
                        setattr(candidate, field, value)
                    candidate.full_clean()
                    if dry_run:
                        totals['updated' if existing else 'created'] += 1
                    else:
                        ProductRecipeItem.objects.update_or_create(
                            product=product,
                            inventory_item=inventory_item,
                            unit=inventory_item.unit,
                            defaults=defaults,
                        )
                        totals['updated' if existing else 'created'] += 1
                except (RowImportError, ValueError, ValidationError) as exc:
                    category = getattr(exc, 'category', 'failed')
                    totals[category] += 1
                    self.stderr.write(self.format_failure(row_number, record, exc))
                except Exception as exc:
                    totals['failed'] += 1
                    self.stderr.write(self.format_failure(row_number, record, f'unexpected error: {exc}'))
            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(' '.join(f'{key}={value}' for key, value in totals.items()))

    def format_failure(self, row_number, record, error):
        return (
            f"Row {row_number}: product='{_text(record['اسم المنتج'])}' "
            f"key='{_text(record['مفتاح المنتج'])}' "
            f"inventory_code='{_text(record['كود مادة المخزون'])}': {error}"
        )
