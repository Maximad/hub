from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalog.models import MenuSection, PrepStation, ProductOption, ProductOptionGroup, ProductOptionGroupAssignment
from core.management.commands.import_inventory_items import ITEM_TYPE_LABELS, UNIT_LABELS, _decimal, _text, _xlsx_rows
from core.models import Category, InventoryItem, Product, ProductRecipeItem
from vendors.models import Vendor

SHEETS = {
    'categories': 'الفئات والأقسام',
    'inventory': 'مواد المخزون',
    'products': 'المنتجات',
    'options': 'خيارات المنتجات',
    'recipes': 'الوصفات',
}
ORDER = ['categories', 'inventory', 'products', 'options', 'recipes']
BATCH_SOURCE = 'Hub_Sweida_Batch_Starter.xlsx'
REVIEWED = 'مراجع'

CATEGORY_HEADERS = ['إجراء الاستيراد','حالة المراجعة','اسم الفئة','Name EN','اسم قسم القائمة','نشط','ظاهر QR','الترتيب','ملاحظات']
INVENTORY_HEADERS = ['إجراء الاستيراد','حالة المراجعة','Code','مادة مخزون','Name EN','نوع المادة','وحدة القياس','الكمية الحالية','حد التنبيه','الكلفة التقديرية للوحدة','المورد المفضل','نشط','ملاحظات']
PRODUCT_HEADERS = ['إجراء الاستيراد','حالة المراجعة','مفتاح المنتج','الاسم العربي','Name EN','DB ID الحالي','المطابقة بواسطة','الفئة','نوع التشغيل','نوع العنصر','النوع الفرعي','السعر ل.س','متاح','ظاهر POS','قابل POS','ظاهر QR','قابل QR','يحتاج تحضير','محطة التحضير','كحولي','يتطلب تأكيد','تيك أواي','الترتيب','الوصف العربي','مسؤول المراجعة','ملاحظات']
OPTION_HEADERS = ['إجراء الاستيراد','حالة المراجعة','كود المجموعة','اسم المجموعة','نوع الاختيار','إلزامي','الحد الأدنى','الحد الأقصى','كود الخيار','اسم الخيار','فرق السعر','افتراضي','نشط','مفاتيح المنتجات','ملاحظات']
RECIPE_HEADERS = ['إجراء الاستيراد','حالة المراجعة','مفتاح المنتج','اسم المنتج','كود مادة المخزون','مادة المخزون','الكمية المدخلة','وحدة الإدخال','وحدة المخزون','الكمية الموحّدة','نسبة الهدر %','نشط','كلفة وحدة المخزون','كلفة السطر','ملاحظات','التحقق']

PRODUCT_TYPE_LABELS = {label: value for value, label in Product.ProductType.choices} | {value: value for value, _ in Product.ProductType.choices}
PRODUCT_ITEM_LABELS = {label: value for value, label in Product.ItemType.choices} | {value: value for value, _ in Product.ItemType.choices}
BEV_LABELS = {label: value for value, label in Product.BeverageType.choices} | {value: value for value, _ in Product.BeverageType.choices}
FOOD_LABELS = {label: value for value, label in Product.FoodType.choices} | {value: value for value, _ in Product.FoodType.choices}
SERVICE_LABELS = {label: value for value, label in Product.ServiceType.choices} | {value: value for value, _ in Product.ServiceType.choices}
SELECTION_LABELS = {label: value for value, label in ProductOptionGroup.SelectionType.choices} | {value: value for value, _ in ProductOptionGroup.SelectionType.choices}
CONVERSIONS = {('g','kg'): Decimal('0.001'), ('kg','g'): Decimal('1000'), ('ml','liter'): Decimal('0.001'), ('liter','ml'): Decimal('1000')}

class BlockingError(Exception): pass

@dataclass
class Plan:
    counts: dict = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    ops: list = field(default_factory=list)
    def inc(self, section, key, n=1): self.counts[section][key] += n
    def error(self, row, msg, section=None):
        self.errors.append(f'Row {row}: {msg}')
        if section: self.inc(section, 'failed')
    def warn(self, row, msg, section=None):
        self.warnings.append(f'Row {row}: {msg}')
        if section: self.inc(section, 'warnings')


def read_rows(path, sheet, headers):
    rows = iter(_xlsx_rows(path, sheet))
    try: actual = [_text(h) for h in next(rows)]
    except StopIteration as exc: raise CommandError(f'Sheet is empty: {sheet}') from exc
    missing = [h for h in headers if h not in actual]
    if missing: raise CommandError(f"Missing required columns in {sheet}: {', '.join(missing)}")
    for n, values in enumerate(rows, start=2):
        rec = {actual[i]: values[i] if i < len(values) else None for i in range(len(actual))}
        if any(_text(v) for v in rec.values()): yield n, rec

def dec(v, default=None):
    d = _decimal(v)
    return default if d is None else d

def integer(v, default=0):
    d = dec(v)
    return default if d is None else int(d)

def boolean(v, *, blank=None):
    t = _text(v)
    if not t: return blank
    if t == 'نعم': return True
    if t == 'لا': return False
    raise BlockingError(f'Unknown boolean value: {t}')

def map_choice(value, mapping, label):
    t = _text(value)
    if t in mapping: return mapping[t]
    raise BlockingError(f'Unknown {label}: {t}')

def products_by_key(key):
    return list(Product.objects.filter(metadata__masharib_menu_code=key))

def resolve_product_key(key):
    matches = products_by_key(key)
    if len(matches) != 1: raise BlockingError(f'Product key {key} matched {len(matches)} products')
    return matches[0]

def unique_vendor(name):
    if not name: return None, None
    matches = list(Vendor.objects.filter(name_ar=name)) + list(Vendor.objects.filter(name_en=name))
    uniq = {v.pk: v for v in matches}
    if len(uniq) == 1: return next(iter(uniq.values())), None
    return None, f'Preferred vendor not uniquely resolved: {name}'

class Command(BaseCommand):
    help = 'Safely import the Hub Sweida batch workbook.'
    def add_arguments(self, parser):
        parser.add_argument('workbook')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--only', choices=ORDER, action='append')

    def handle(self, *args, **options):
        path = Path(options['workbook'])
        if not path.exists(): raise CommandError(f'Workbook does not exist: {path}')
        only = set(options['only'] or ORDER)
        plan = Plan()
        # Validate the whole workbook while applying planned operations inside a
        # rollback-only transaction so later sheets can see earlier would-be rows.
        with transaction.atomic():
            self.build_plan(path, only, plan, execute=True)
            transaction.set_rollback(True)
        self.print_plan(plan)
        if plan.errors:
            for e in plan.errors: self.stderr.write(e)
            raise CommandError(f'Blocking validation errors: {len(plan.errors)}')
        for w in plan.warnings: self.stderr.write(w)
        if not options['apply']:
            self.stdout.write('DRY RUN — no database changes made')
            return
        apply_plan = Plan()
        with transaction.atomic():
            self.build_plan(path, only, apply_plan, execute=True)
            if apply_plan.errors:
                raise CommandError(f'Blocking validation errors before apply: {len(apply_plan.errors)}')
        self.stdout.write('APPLIED — database changes committed')

    def build_plan(self, path, only, plan, execute=False):
        executed = 0
        for section, method in (
            ('categories', self.categories),
            ('inventory', self.inventory),
            ('products', self.products),
            ('options', self.options),
            ('recipes', self.recipes),
        ):
            if section not in only:
                continue
            method(path, plan)
            if execute:
                for op in plan.ops[executed:]:
                    op()
                executed = len(plan.ops)

    def categories(self, path, plan):
        for row, r in read_rows(path, SHEETS['categories'], CATEGORY_HEADERS):
            act = _text(r['إجراء الاستيراد'])
            if act == 'SKIP': plan.inc('categories','skipped'); continue
            ca, ms = _text(r['اسم الفئة']), _text(r['اسم قسم القائمة'])
            try:
                cat = Category.objects.filter(name_ar=ca).first(); sec = MenuSection.objects.filter(name_ar=ms).first()
                if act == 'MATCH_ONLY':
                    if not cat or not sec: raise BlockingError('Category/MenuSection missing for MATCH_ONLY')
                    plan.inc('categories','matched')
                elif act == 'CREATE_HIDDEN':
                    if cat: plan.inc('categories','matched')
                    else:
                        plan.inc('categories','created_hidden'); plan.ops.append(lambda ca=ca,r=r: self.save_obj(Category(name_ar=ca,name_en=_text(r['Name EN']))))
                    if sec: plan.inc('categories','matched')
                    else:
                        plan.inc('categories','created_hidden'); plan.ops.append(lambda ms=ms,r=r: self.save_obj(MenuSection(name_ar=ms,name_en=_text(r['Name EN']),is_active=False,visible_on_qr=False,sort_order=integer(r['الترتيب']))))
                else: raise BlockingError(f'Unsupported action: {act}')
            except (BlockingError, ValidationError, ValueError) as e: plan.error(row, str(e), 'categories')

    def inventory(self, path, plan):
        for row, r in read_rows(path, SHEETS['inventory'], INVENTORY_HEADERS):
            act=_text(r['إجراء الاستيراد']); code=_text(r['Code']); name=_text(r['مادة مخزون'])
            if act=='SKIP': plan.inc('inventory','skipped'); continue
            try:
                item=InventoryItem.objects.filter(code=code).first()
                if act=='MATCH_ONLY':
                    if not item: raise BlockingError(f'Inventory code missing: {code}')
                    if item.name_ar != name: plan.warn(row, f'Inventory Arabic name differs for code {code}', 'inventory')
                    plan.inc('inventory','matched')
                elif act=='CREATE_INACTIVE':
                    if item: plan.inc('inventory','matched'); continue
                    name_matches=list(InventoryItem.objects.filter(name_ar=name)[:2])
                    if len(name_matches)==1: plan.inc('inventory','name_conflicts'); continue
                    if len(name_matches)>1: raise BlockingError(f'Ambiguous inventory Arabic name: {name}')
                    vendor, warn = unique_vendor(_text(r['المورد المفضل']))
                    if warn: plan.warn(row, warn, 'inventory')
                    defaults=dict(code=code,name_ar=name,name_en=_text(r['Name EN']),item_type=map_choice(r['نوع المادة'],ITEM_TYPE_LABELS,'inventory type'),unit=map_choice(r['وحدة القياس'],UNIT_LABELS,'unit'),current_quantity=Decimal('0'),low_stock_threshold=dec(r['حد التنبيه']),estimated_unit_cost_syp=dec(r['الكلفة التقديرية للوحدة']),preferred_vendor=vendor,is_active=False,notes=_text(r['ملاحظات']))
                    plan.inc('inventory','created_inactive'); plan.ops.append(lambda defaults=defaults: self.save_obj(InventoryItem(**defaults)))
                else: raise BlockingError(f'Unsupported action: {act}')
            except (BlockingError, ValidationError, ValueError) as e: plan.error(row, str(e), 'inventory')

    def products(self, path, plan):
        for row,r in read_rows(path,SHEETS['products'],PRODUCT_HEADERS):
            act=_text(r['إجراء الاستيراد']); key=_text(r['مفتاح المنتج']); name=_text(r['الاسم العربي'])
            if act=='SKIP': plan.inc('products','skipped'); continue
            try:
                km=products_by_key(key) if key else []
                if act=='MATCH_ONLY':
                    if len(km)>1: plan.inc('products','duplicate_key_errors'); raise BlockingError(f'Duplicate product key: {key}')
                    if len(km)!=1: raise BlockingError(f'Missing product key: {key}')
                    p=km[0]; plan.inc('products','matched_by_key')
                    if _text(r['DB ID الحالي']) and str(p.pk)!=_text(r['DB ID الحالي']): plan.warn(row,'DB ID audit mismatch','products')
                    if p.name_ar!=name: plan.warn(row,'Product Arabic name differs','products')
                elif act in {'UPDATE_KEY_ONLY','CREATE_INACTIVE'}:
                    if len(km)>1: plan.inc('products','duplicate_key_errors'); raise BlockingError(f'Duplicate product key: {key}')
                    if len(km)==1: plan.inc('products','matched_by_key'); continue
                    names=list(Product.objects.filter(name_ar=name)[:2])
                    if len(names)>1: plan.inc('products','ambiguous_name_errors'); raise BlockingError(f'Ambiguous product Arabic name: {name}')
                    if len(names)==1:
                        p=names[0]; old=(p.metadata or {}).get('masharib_menu_code')
                        if old and old!=key: raise BlockingError(f'Product already has different key: {old}')
                        if not old:
                            plan.inc('products','keys_added'); plan.ops.append(lambda pk=p.pk,key=key: self.add_key(pk,key))
                        plan.inc('products','matched_by_name'); continue
                    if act=='UPDATE_KEY_ONLY': raise BlockingError(f'Product name not found: {name}')
                    product=self.make_product(r,key,name); plan.inc('products','created_inactive'); plan.ops.append(lambda product=product,r=r: self.save_product(product, _text(r['الفئة'])))
                else: raise BlockingError(f'Unsupported action: {act}')
            except (BlockingError, ValidationError, ValueError) as e: plan.error(row,str(e),'products')

    def make_product(self,r,key,name):
        cat=Category.objects.filter(name_ar=_text(r['الفئة'])).first()
        if not cat: raise BlockingError(f"Category missing: {_text(r['الفئة'])}")
        station=None
        if _text(r['محطة التحضير']):
            station=PrepStation.objects.filter(code=_text(r['محطة التحضير'])).first()
            if not station: raise BlockingError(f"PrepStation missing: {_text(r['محطة التحضير'])}")
        item=map_choice(r['نوع العنصر'],PRODUCT_ITEM_LABELS,'product item type')
        p=Product(category=cat,name_ar=name,name_en=_text(r['Name EN']),description_ar=_text(r['الوصف العربي']),price_syp=integer(r['السعر ل.س'],0),product_type=map_choice(r['نوع التشغيل'],PRODUCT_TYPE_LABELS,'product type'),item_type=item,prep_station_ref=station,requires_preparation=boolean(r['يحتاج تحضير'],blank=False),available_for_takeaway=boolean(r['تيك أواي'],blank=False),is_alcoholic=boolean(r['كحولي'],blank=False),requires_staff_confirmation=boolean(r['يتطلب تأكيد'],blank=False),sort_order=integer(r['الترتيب']),is_available=False,visible_on_pos=False,orderable_on_pos=False,visible_on_qr=False,orderable_on_qr=False,metadata={'masharib_menu_code':key,'batch_source':BATCH_SOURCE,'batch_review_status':_text(r['حالة المراجعة'])})
        p.age_restricted=p.is_alcoholic; sub=_text(r['النوع الفرعي'])
        if sub:
            if item=='beverage': p.beverage_type=map_choice(sub,BEV_LABELS,'beverage subtype')
            elif item=='food': p.food_type=map_choice(sub,FOOD_LABELS,'food subtype')
            elif item=='service': p.service_type=map_choice(sub,SERVICE_LABELS,'service subtype')
        return p

    def options(self,path,plan):
        planned_groups = {}
        planned_options = set()
        planned_assignments = set()
        for row,r in read_rows(path,SHEETS['options'],OPTION_HEADERS):
            act=_text(r['إجراء الاستيراد']);
            if act=='SKIP': plan.inc('options','skipped'); continue
            op_mark = len(plan.ops)
            added_group_code = None
            added_option_key = None
            added_assignment_keys = []
            try:
                group_code = _text(r['كود المجموعة'])
                option_code = _text(r['كود الخيار'])
                group=ProductOptionGroup.objects.filter(code=group_code).first()
                if not group:
                    group = planned_groups.get(group_code)
                if act=='MATCH_ONLY' and not group: raise BlockingError('Option group missing')
                if not group and act=='CREATE_INACTIVE':
                    group=ProductOptionGroup(code=group_code,name_ar=_text(r['اسم المجموعة']),selection_type=map_choice(r['نوع الاختيار'],SELECTION_LABELS,'selection type'),is_required=boolean(r['إلزامي'],blank=False),min_selected=integer(r['الحد الأدنى']),max_selected=dec(r['الحد الأقصى']),is_active=False)
                    planned_groups[group_code] = group
                    added_group_code = group_code
                    plan.inc('options','groups_created_inactive'); plan.ops.append(lambda g=group: self.save_obj(g))
                else: plan.inc('options','groups_matched')

                option_key = (group_code, option_code)
                db_group = group if getattr(group, 'pk', None) else None
                opt_exists = bool(db_group and ProductOption.objects.filter(group=db_group,code=option_code).exists())
                if act=='MATCH_ONLY' and not opt_exists: raise BlockingError('Option missing')
                if opt_exists or option_key in planned_options:
                    plan.inc('options','options_matched')
                else:
                    planned_options.add(option_key)
                    added_option_key = option_key
                    plan.inc('options','options_created_inactive'); plan.ops.append(lambda r=r: self.create_option(r))
                for key in [x.strip() for x in _text(r['مفاتيح المنتجات']).split(',') if x.strip()]:
                    p=resolve_product_key(key)
                    assignment_key = (p.pk, group_code)
                    assignment_exists = bool(db_group and ProductOptionGroupAssignment.objects.filter(product=p,group=db_group).exists())
                    if assignment_exists or assignment_key in planned_assignments:
                        plan.inc('options','assignments_matched')
                    else:
                        planned_assignments.add(assignment_key)
                        added_assignment_keys.append(assignment_key)
                        plan.inc('options','assignments_created'); plan.ops.append(lambda pk=p.pk,code=group_code,active=(boolean(r['نشط'],blank=False) if act=='MATCH_ONLY' else False): self.create_assignment(pk,code,active))
            except (BlockingError, ValidationError, ValueError) as e:
                del plan.ops[op_mark:]
                if added_group_code:
                    planned_groups.pop(added_group_code, None)
                if added_option_key:
                    planned_options.discard(added_option_key)
                for assignment_key in added_assignment_keys:
                    planned_assignments.discard(assignment_key)
                plan.error(row,str(e),'options')

    def recipes(self,path,plan):
        for row,r in read_rows(path,SHEETS['recipes'],RECIPE_HEADERS):
            act=_text(r['إجراء الاستيراد'])
            if act=='SKIP': plan.inc('recipes','skipped'); continue
            try:
                if act=='UPSERT_ACTIVE' and _text(r['حالة المراجعة']) != REVIEWED: raise BlockingError('UPSERT_ACTIVE requires حالة المراجعة = مراجع')
                p=resolve_product_key(_text(r['مفتاح المنتج'])); inv=InventoryItem.objects.filter(code=_text(r['كود مادة المخزون'])).first()
                if not inv: raise BlockingError('Inventory code missing')
                qty=dec(r['الكمية المدخلة']); iu=map_choice(r['وحدة الإدخال'],UNIT_LABELS,'input unit')
                if iu==inv.unit: norm=qty
                elif (iu,inv.unit) in CONVERSIONS: norm=qty*CONVERSIONS[(iu,inv.unit)]
                else: raise BlockingError(f'Unsupported unit conversion: {iu} -> {inv.unit}')
                norm=norm.quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)
                if norm <= 0: raise BlockingError('Recipe quantity must be positive')
                waste=dec(r['نسبة الهدر %'],Decimal('0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                existing=ProductRecipeItem.objects.filter(product=p,inventory_item=inv,unit=inv.unit).first()
                if act=='UPSERT_INACTIVE' and existing and existing.is_active:
                    plan.inc('recipes','active_existing_preserved'); plan.warn(row,'Active recipe preserved','recipes'); continue
                key='created_active' if act=='UPSERT_ACTIVE' and not existing else 'updated_active' if act=='UPSERT_ACTIVE' else 'created_inactive' if not existing else 'updated_inactive'
                plan.inc('recipes',key); plan.ops.append(lambda pk=p.pk,ik=inv.pk,unit=inv.unit,qty=norm,waste=waste,notes=_text(r['ملاحظات']),active=(act=='UPSERT_ACTIVE'): self.upsert_recipe(pk,ik,unit,qty,waste,notes,active))
            except (BlockingError, ValidationError, ValueError) as e: plan.error(row,str(e),'recipes')

    def save_obj(self,obj): obj.full_clean(); obj.save()
    def save_product(self,p,section_name):
        p.full_clean(); p.save(); sec=MenuSection.objects.filter(name_ar=section_name).first();
        if sec: p.menu_sections.add(sec)
    def add_key(self,pk,key):
        p=Product.objects.get(pk=pk); md=dict(p.metadata or {}); md['masharib_menu_code']=key; p.metadata=md; p.full_clean(); p.save(update_fields=['metadata','updated_at'])
    def create_option(self,r):
        g=ProductOptionGroup.objects.get(code=_text(r['كود المجموعة'])); o=ProductOption(group=g,code=_text(r['كود الخيار']),name_ar=_text(r['اسم الخيار']),price_delta_syp=integer(r['فرق السعر']),is_default=boolean(r['افتراضي'],blank=False),is_active=False); self.save_obj(o)
    def create_assignment(self,pk,code,active):
        g=ProductOptionGroup.objects.get(code=code); p=Product.objects.get(pk=pk); a,created=ProductOptionGroupAssignment.objects.get_or_create(product=p,group=g,defaults={'is_active':active});
        if created: a.full_clean()
    def upsert_recipe(self,pk,ik,unit,qty,waste,notes,active):
        obj,_=ProductRecipeItem.objects.get_or_create(product_id=pk,inventory_item_id=ik,unit=unit,defaults={'quantity_per_unit':qty,'waste_factor_percent':waste,'notes':notes,'is_active':active})
        obj.quantity_per_unit=qty; obj.waste_factor_percent=waste; obj.notes=notes; obj.is_active=active; obj.full_clean(); obj.save()
    def print_plan(self,plan):
        self.stdout.write('Import plan')
        for sec in ORDER:
            if plan.counts[sec]: self.stdout.write(f"{sec}: " + ' '.join(f'{k}={v}' for k,v in sorted(plan.counts[sec].items())))
        self.stdout.write(f'warnings={len(plan.warnings)} errors={len(plan.errors)}')
