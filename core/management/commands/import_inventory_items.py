from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
import posixpath
import re
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import InventoryItem
from vendors.models import Vendor

SHEET_NAME = 'مواد المخزون'
NS = {'a': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL_NS = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
MAIN_NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'

HEADER_TO_FIELD = {
    'مادة مخزون': 'name_ar',
    'Name EN': 'name_en',
    'Code': 'code',
    'نوع المادة': 'item_type',
    'وحدة القياس': 'unit',
    'الكمية الحالية': 'current_quantity',
    'حد التنبيه': 'low_stock_threshold',
    'الكلفة التقديرية للوحدة': 'estimated_unit_cost_syp',
    'المورد المفضل': 'preferred_vendor',
    'ملاحظات': 'notes',
}

ITEM_TYPE_LABELS = {
    label: value for value, label in InventoryItem.ItemType.choices
} | {
    value: value for value, _label in InventoryItem.ItemType.choices
}
UNIT_LABELS = {
    label: value for value, label in InventoryItem.Unit.choices
} | {
    value: value for value, _label in InventoryItem.Unit.choices
}


def _text(value):
    if value is None:
        return ''
    return str(value).strip()


def _decimal(value, *, default_zero=False):
    value = _text(value).replace(',', '')
    if value == '':
        return Decimal('0') if default_zero else None
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f'Invalid decimal value: {value}') from exc


def _column_index(cell_ref):
    match = re.match(r'([A-Z]+)', cell_ref)
    number = 0
    for char in match.group(1):
        number = number * 26 + ord(char) - 64
    return number - 1


def _xlsx_rows(path, sheet_name):
    with ZipFile(path) as archive:
        shared_strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            root = ET.fromstring(archive.read('xl/sharedStrings.xml'))
            for si in root.findall(f'{MAIN_NS}si'):
                shared_strings.append(''.join(t.text or '' for t in si.findall(f'.//{MAIN_NS}t')))

        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        relationship_id = None
        for sheet in workbook.findall(f'.//{MAIN_NS}sheet'):
            if sheet.attrib.get('name') == sheet_name:
                relationship_id = sheet.attrib[REL_NS + 'id']
                break
        if not relationship_id:
            raise CommandError(f'Sheet not found: {sheet_name}')

        rels = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        target = None
        for rel in rels:
            if rel.attrib['Id'] == relationship_id:
                target = rel.attrib['Target']
                break
        if not target:
            raise CommandError(f'Worksheet relationship not found for sheet: {sheet_name}')

        worksheet_path = target.lstrip('/') if target.startswith('/xl/') else posixpath.normpath(posixpath.join('xl', target))
        worksheet = ET.fromstring(archive.read(worksheet_path))

        def cell_value(cell):
            value = cell.find(f'{MAIN_NS}v')
            if value is None:
                inline = cell.find(f'{MAIN_NS}is/{MAIN_NS}t')
                return inline.text if inline is not None else None
            raw = value.text
            if cell.attrib.get('t') == 's':
                return shared_strings[int(raw)]
            return raw

        for row in worksheet.findall(f'.//{MAIN_NS}sheetData/{MAIN_NS}row'):
            values = []
            for cell in row.findall(f'{MAIN_NS}c'):
                index = _column_index(cell.attrib['r'])
                while len(values) <= index:
                    values.append(None)
                values[index] = cell_value(cell)
            yield values


def read_inventory_rows(path, sheet_name=SHEET_NAME):
    rows = iter(_xlsx_rows(path, sheet_name))
    try:
        headers = [_text(header) for header in next(rows)]
    except StopIteration as exc:
        raise CommandError(f'Sheet is empty: {sheet_name}') from exc
    for missing in set(HEADER_TO_FIELD) - set(headers):
        raise CommandError(f'Missing required column: {missing}')
    for excel_row_number, values in enumerate(rows, start=2):
        record = {headers[index]: values[index] if index < len(values) else None for index in range(len(headers))}
        if not any(_text(value) for value in record.values()):
            continue
        yield excel_row_number, record


class SkipRow(Exception):
    pass


def build_item_defaults(record):
    code = _text(record['Code'])
    if not code:
        raise ValueError('Code is required')

    item_type_label = _text(record['نوع المادة'])
    unit_label = _text(record['وحدة القياس'])
    if item_type_label not in ITEM_TYPE_LABELS:
        raise ValueError(f'Unknown item type: {item_type_label}')
    if unit_label not in UNIT_LABELS:
        raise ValueError(f'Unknown unit: {unit_label}')

    vendor_name = _text(record['المورد المفضل'])
    vendor = None
    if vendor_name:
        vendor = Vendor.objects.filter(name_ar=vendor_name).first() or Vendor.objects.filter(name_en=vendor_name).first()
        if not vendor:
            raise SkipRow(f'Preferred vendor not found: {vendor_name}')

    return code, {
        'name_ar': _text(record['مادة مخزون']),
        'name_en': _text(record['Name EN']),
        'item_type': ITEM_TYPE_LABELS[item_type_label],
        'unit': UNIT_LABELS[unit_label],
        'current_quantity': _decimal(record['الكمية الحالية'], default_zero=True),
        'low_stock_threshold': _decimal(record['حد التنبيه']),
        'estimated_unit_cost_syp': _decimal(record['الكلفة التقديرية للوحدة']),
        'preferred_vendor': vendor,
        'notes': _text(record['ملاحظات']),
    }


class Command(BaseCommand):
    help = 'Import inventory items from the Hub Sweida inventory workbook.'

    def add_arguments(self, parser):
        parser.add_argument('workbook', help='Path to the .xlsx inventory workbook')
        parser.add_argument('--dry-run', action='store_true', help='Validate and report changes without saving them')

    def handle(self, *args, **options):
        workbook = Path(options['workbook'])
        dry_run = options['dry_run']
        if not workbook.exists():
            raise CommandError(f'Workbook does not exist: {workbook}')

        totals = {'created': 0, 'updated': 0, 'skipped': 0, 'failed': 0}
        with transaction.atomic():
            for row_number, record in read_inventory_rows(workbook):
                try:
                    code, defaults = build_item_defaults(record)
                    existing = InventoryItem.objects.filter(code=code).first()
                    candidate = existing or InventoryItem(code=code)
                    for field, value in defaults.items():
                        setattr(candidate, field, value)
                    candidate.full_clean()
                    if dry_run:
                        totals['updated' if existing else 'created'] += 1
                    else:
                        _item, created = InventoryItem.objects.update_or_create(code=code, defaults=defaults)
                        totals['created' if created else 'updated'] += 1
                except SkipRow as exc:
                    totals['skipped'] += 1
                    self.stderr.write(f'Row {row_number}: skipped: {exc}')
                except (ValueError, ValidationError) as exc:
                    totals['failed'] += 1
                    self.stderr.write(f'Row {row_number}: {exc}')
                except Exception as exc:
                    totals['failed'] += 1
                    self.stderr.write(f'Row {row_number}: unexpected error: {exc}')
            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(
            f"created={totals['created']} updated={totals['updated']} "
            f"skipped={totals['skipped']} failed={totals['failed']}"
        )
