from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

from django.core.management import call_command, CommandError
from django.test import TestCase

from catalog.models import MenuSection, PrepStation, ProductOption, ProductOptionGroup, ProductOptionGroupAssignment
from core.management.commands.import_hub_batch import CATEGORY_HEADERS, INVENTORY_HEADERS, OPTION_HEADERS, PRODUCT_HEADERS, RECIPE_HEADERS, SHEETS
from core.models import Category, InventoryItem, Product, ProductRecipeItem, StockMovement


def col(n):
    s = ''
    while n >= 0:
        s = chr(65 + n % 26) + s; n = n // 26 - 1
    return s


def write_book(path, sheets):
    strings = []; idx = {}
    def shared(v):
        v = '' if v is None else str(v)
        if v not in idx: idx[v] = len(strings); strings.append(v)
        return idx[v]
    with ZipFile(path, 'w', ZIP_DEFLATED) as z:
        overrides = ['<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>','<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>']
        sheet_tags=[]; rel_tags=[]
        for i,(name,rows) in enumerate(sheets.items(), start=1):
            overrides.append(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
            sheet_tags.append(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>')
            rel_tags.append(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>')
            row_xml=[]
            for rnum,row in enumerate(rows, start=1):
                cells=[]
                for cnum,val in enumerate(row):
                    if val is None: continue
                    cells.append(f'<c r="{col(cnum)}{rnum}" t="s"><v>{shared(val)}</v></c>')
                row_xml.append(f'<row r="{rnum}">{"".join(cells)}</row>')
            z.writestr(f'xl/worksheets/sheet{i}.xml', f'<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{"".join(row_xml)}</sheetData></worksheet>')
        z.writestr('[Content_Types].xml', f'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>{"".join(overrides)}</Types>')
        z.writestr('_rels/.rels', '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', f'<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{"".join(sheet_tags)}</sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', f'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{"".join(rel_tags)}</Relationships>')
        z.writestr('xl/sharedStrings.xml', f'<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">{"".join(f"<si><t>{s}</t></si>" for s in strings)}</sst>')


def base_sheets(**rows):
    return {
        SHEETS['categories']: [CATEGORY_HEADERS] + rows.get('categories', []),
        SHEETS['inventory']: [INVENTORY_HEADERS] + rows.get('inventory', []),
        SHEETS['products']: [PRODUCT_HEADERS] + rows.get('products', []),
        SHEETS['options']: [OPTION_HEADERS] + rows.get('options', []),
        SHEETS['recipes']: [RECIPE_HEADERS] + rows.get('recipes', []),
        'لوحة الدفعة': [['info']], 'قائمة المراجعة': [['info']],
    }


def cat_row(action='CREATE_HIDDEN', cat='مشروبات'):
    return [action,'بحاجة مراجعة',cat,'Drinks',cat,'نعم','نعم','7','']

def inv_row(action='CREATE_INACTIVE', code='ING-SUGAR', name='سكر', qty='99'):
    return [action,'بحاجة مراجعة',code,name,'Sugar','مكوّن','كغ',qty,'1.5','10','','نعم','note']

def prod_row(action='CREATE_INACTIVE', key='P1', name='قهوة', cat='مشروبات'):
    return [action,'بحاجة مراجعة',key,name,'Coffee','','',cat,'مشروب','beverage','coffee','5000','نعم','نعم','نعم','نعم','نعم','نعم','bar','لا','لا','نعم','3','desc','','']

def opt_row(action='CREATE_INACTIVE', key='P1'):
    return [action,'بحاجة مراجعة','size','الحجم','single','لا','0','1','large','كبير','500','لا','نعم',key,'']

def rec_row(action='UPSERT_INACTIVE', key='P1', code='ING-SUGAR', qty='1500', unit='غ', waste='1.234'):
    return [action,'بحاجة مراجعة',key,'',code,'',qty,unit,'','','{}'.format(waste),'نعم','','','','']

class ImportHubBatchCommandTests(TestCase):
    def setUp(self):
        PrepStation.objects.get_or_create(code='bar', defaults={'name_ar':'البار'})

    def run_book(self, sheets, *args):
        tmp = TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'batch.xlsx'; write_book(path, sheets)
        out, err = StringIO(), StringIO()
        call_command('import_hub_batch', str(path), *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_dry_run_default_and_apply_creates_hidden_records(self):
        sheets = base_sheets(categories=[cat_row()], inventory=[inv_row()], products=[prod_row()])
        out, _ = self.run_book(sheets)
        self.assertIn('DRY RUN', out); self.assertEqual(Product.objects.count(), 0)
        out, _ = self.run_book(sheets, '--apply')
        self.assertIn('APPLIED', out)
        self.assertFalse(MenuSection.objects.get(name_ar='مشروبات').is_active)
        self.assertFalse(InventoryItem.objects.get(code='ING-SUGAR').is_active)
        self.assertEqual(InventoryItem.objects.get(code='ING-SUGAR').current_quantity, Decimal('0.000'))
        p = Product.objects.get(metadata__masharib_menu_code='P1')
        self.assertFalse(p.is_available); self.assertFalse(p.visible_on_pos); self.assertEqual(p.price_syp, 5000)

    def test_repeated_import_no_duplicates_options_and_recipe_conversion_rounding(self):
        sheets = base_sheets(categories=[cat_row()], inventory=[inv_row()], products=[prod_row()], options=[opt_row()], recipes=[rec_row()])
        self.run_book(sheets, '--apply'); self.run_book(sheets, '--apply')
        self.assertEqual(Category.objects.count(), 1); self.assertEqual(InventoryItem.objects.count(), 1); self.assertEqual(Product.objects.count(), 1)
        self.assertEqual(ProductOptionGroup.objects.count(), 1); self.assertEqual(ProductOption.objects.count(), 1); self.assertEqual(ProductOptionGroupAssignment.objects.count(), 1)
        recipe = ProductRecipeItem.objects.get()
        self.assertEqual(recipe.quantity_per_unit, Decimal('1.500')); self.assertEqual(recipe.waste_factor_percent, Decimal('1.23'))
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_match_only_never_changes_existing_inventory_quantity_or_product(self):
        c = Category.objects.create(name_ar='مشروبات'); MenuSection.objects.create(name_ar='مشروبات')
        item = InventoryItem.objects.create(code='ING-SUGAR', name_ar='سكر قديم', current_quantity=Decimal('8'), unit=InventoryItem.Unit.KG)
        p = Product.objects.create(category=c, name_ar='قهوة', price_syp=100, metadata={'masharib_menu_code':'P1'}, is_available=True)
        sheets = base_sheets(categories=[cat_row('MATCH_ONLY')], inventory=[inv_row('MATCH_ONLY', name='سكر جديد')], products=[prod_row('MATCH_ONLY')])
        out, err = self.run_book(sheets, '--apply')
        item.refresh_from_db(); p.refresh_from_db()
        self.assertEqual(item.current_quantity, Decimal('8.000')); self.assertEqual(item.name_ar, 'سكر قديم'); self.assertEqual(p.price_syp, 100); self.assertTrue(p.is_available)
        self.assertIn('Arabic name differs', err)

    def test_update_key_only_changes_metadata_only_and_reuses_name_match(self):
        c = Category.objects.create(name_ar='مشروبات'); MenuSection.objects.create(name_ar='مشروبات')
        Product.objects.create(category=c, name_ar='قهوة', price_syp=123, metadata={'other':'x'}, is_available=True)
        sheets = base_sheets(products=[prod_row('UPDATE_KEY_ONLY')])
        self.run_book(sheets, '--apply')
        p = Product.objects.get(name_ar='قهوة')
        self.assertEqual(p.metadata, {'other':'x','masharib_menu_code':'P1'}); self.assertEqual(p.price_syp, 123); self.assertTrue(p.is_available)

    def test_blocking_error_rolls_back_all_changes_and_duplicate_ambiguous_fail(self):
        c = Category.objects.create(name_ar='مشروبات')
        Product.objects.create(category=c, name_ar='أ', price_syp=1, metadata={'masharib_menu_code':'DUP'})
        Product.objects.create(category=c, name_ar='ب', price_syp=1, metadata={'masharib_menu_code':'DUP'})
        sheets = base_sheets(categories=[cat_row()], products=[prod_row('MATCH_ONLY', key='DUP')])
        with self.assertRaises(CommandError): self.run_book(sheets, '--apply')
        self.assertFalse(MenuSection.objects.filter(name_ar='مشروبات').exists())
        Product.objects.create(category=c, name_ar='مكرر', price_syp=1); Product.objects.create(category=c, name_ar='مكرر', price_syp=2)
        sheets = base_sheets(products=[prod_row('CREATE_INACTIVE', key='NEW', name='مكرر')])
        with self.assertRaises(CommandError): self.run_book(sheets, '--apply')

    def test_active_recipe_preserved_and_upsert_active_requires_reviewed(self):
        c = Category.objects.create(name_ar='مشروبات')
        p = Product.objects.create(category=c, name_ar='قهوة', price_syp=1, metadata={'masharib_menu_code':'P1'})
        i = InventoryItem.objects.create(code='ING-SUGAR', name_ar='سكر', unit=InventoryItem.Unit.KG)
        ProductRecipeItem.objects.create(product=p, inventory_item=i, unit=i.unit, quantity_per_unit=Decimal('2'), waste_factor_percent=Decimal('0'), is_active=True)
        sheets = base_sheets(recipes=[rec_row()])
        self.run_book(sheets, '--apply')
        self.assertEqual(ProductRecipeItem.objects.get().quantity_per_unit, Decimal('2.000'))
        sheets = base_sheets(recipes=[rec_row('UPSERT_ACTIVE')])
        with self.assertRaises(CommandError): self.run_book(sheets, '--apply')
