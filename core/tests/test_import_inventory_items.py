from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile, ZIP_DEFLATED

from django.core.management import call_command
from django.test import TestCase

from core.management.commands.import_inventory_items import read_inventory_rows
from core.models import InventoryItem
from vendors.models import Vendor


def write_xlsx(path, rows, sheet_name='مواد المخزون'):
    strings = []
    string_index = {}

    def shared(value):
        value = '' if value is None else str(value)
        if value not in string_index:
            string_index[value] = len(strings)
            strings.append(value)
        return string_index[value]

    row_xml = []
    for r_idx, row in enumerate(rows, start=1):
        cells = []
        for c_idx, value in enumerate(row):
            if value is None:
                continue
            col = chr(ord('A') + c_idx)
            cells.append(f'<c r="{col}{r_idx}" t="s"><v>{shared(value)}</v></c>')
        row_xml.append(f'<row r="{r_idx}">{"".join(cells)}</row>')

    shared_xml = ''.join(f'<si><t>{value}</t></si>' for value in strings)
    with ZipFile(path, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/></Types>')
        archive.writestr('_rels/.rels', '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr('xl/workbook.xml', f'<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/sharedStrings.xml', f'<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(strings)}" uniqueCount="{len(strings)}">{shared_xml}</sst>')
        archive.writestr('xl/worksheets/sheet1.xml', f'<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{"".join(row_xml)}</sheetData></worksheet>')


HEADERS = ['مادة مخزون', 'Name EN', 'Code', 'نوع المادة', 'وحدة القياس', 'الكمية الحالية', 'حد التنبيه', 'الكلفة التقديرية للوحدة', 'المورد المفضل', 'ملاحظات']


class ImportInventoryItemsCommandTests(TestCase):
    def test_reads_inventory_sheet_rows(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inventory.xlsx'
            write_xlsx(path, [HEADERS, ['بطاطا', 'Potato', 'ING-VEG-POTATO', 'مكوّن', 'كغ', '', '', '7000.25', '', 'note']])
            rows = list(read_inventory_rows(path))
        self.assertEqual(rows[0][0], 2)
        self.assertEqual(rows[0][1]['Code'], 'ING-VEG-POTATO')

    def test_import_creates_and_updates_by_code_preserving_decimals(self):
        vendor = Vendor.objects.create(name_ar='مورد', vendor_type=Vendor.VendorType.SUPPLIER)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inventory.xlsx'
            write_xlsx(path, [HEADERS, ['بطاطا', 'Potato', 'ING-VEG-POTATO', 'مكوّن', 'كغ', '', '2.500', '7000.25', vendor.name_ar, 'note']])
            out = StringIO()
            call_command('import_inventory_items', str(path), stdout=out)
            call_command('import_inventory_items', str(path), stdout=out)

        self.assertEqual(InventoryItem.objects.count(), 1)
        item = InventoryItem.objects.get(code='ING-VEG-POTATO')
        self.assertEqual(item.item_type, InventoryItem.ItemType.INGREDIENT)
        self.assertEqual(item.unit, InventoryItem.Unit.KG)
        self.assertEqual(item.current_quantity, Decimal('0.000'))
        self.assertEqual(item.low_stock_threshold, Decimal('2.500'))
        self.assertEqual(item.estimated_unit_cost_syp, Decimal('7000.25'))
        self.assertEqual(item.preferred_vendor, vendor)
        self.assertIn('created=1 updated=0 skipped=0 failed=0', out.getvalue())
        self.assertIn('created=0 updated=1 skipped=0 failed=0', out.getvalue())

    def test_dry_run_and_unknown_vendor_do_not_save(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inventory.xlsx'
            write_xlsx(path, [HEADERS, ['سكر', 'Sugar', 'ING-SUGAR', 'مكوّن', 'كغ', '1.250', '', '', '', ''], ['ملح', 'Salt', 'ING-SALT', 'مكوّن', 'كغ', '1', '', '', 'مجهول', '']])
            out = StringIO(); err = StringIO()
            call_command('import_inventory_items', str(path), '--dry-run', stdout=out, stderr=err)

        self.assertEqual(InventoryItem.objects.count(), 0)
        self.assertIn('created=1 updated=0 skipped=1 failed=0', out.getvalue())
        self.assertIn('Row 3: skipped: Preferred vendor not found', err.getvalue())
