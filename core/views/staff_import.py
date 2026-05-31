import csv
import io
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from catalog.models import MenuSection, ProductMedia, ProductOptionGroup, ProductOptionGroupAssignment, PrepStation, Tag
from core.models import ActivityLog, Category, InternetPackage, Member, Product, Room, TableArea
from events.models import Event, EventTicketType
from members.models import MembershipPlan, MembershipSubscription, MemberCreditLedger
from vendors.models import Vendor

IMPORT_MODES = {
    'dry_run': 'معاينة فقط',
    'create_only': 'إنشاء فقط',
    'update_only': 'تحديث فقط',
    'create_and_update': 'إنشاء وتحديث',
}

BOOLEAN_TRUE = {'true', 'yes', '1', 'نعم', 'صح', 'y'}
BOOLEAN_FALSE = {'false', 'no', '0', 'لا', 'خطأ', 'n'}


@dataclass
class RowResult:
    row_number: int
    raw: dict
    cleaned: dict = field(default_factory=dict)
    action: str = 'skip'
    key_label: str = ''
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    matched_id: int | None = None


class BaseImporter:
    slug = ''
    title = ''
    description = ''
    headers = []
    sample = {}
    model_label = ''

    def __init__(self, mode='dry_run'):
        self.mode = mode if mode in IMPORT_MODES else 'dry_run'

    def template_response(self):
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=self.headers)
        writer.writeheader()
        writer.writerow({header: self.sample.get(header, '') for header in self.headers})
        response = HttpResponse('\ufeff' + output.getvalue(), content_type='text/csv; charset=utf-8-sig')
        response['Content-Disposition'] = f'attachment; filename="{self.slug}-template.csv"'
        return response

    def parse_csv(self, csv_text):
        errors = []
        rows = []
        try:
            sample = csv_text[:4096]
            dialect = csv.Sniffer().sniff(sample) if sample.strip() else csv.excel
        except csv.Error:
            dialect = csv.excel
        try:
            reader = csv.DictReader(io.StringIO(csv_text), dialect=dialect)
            if not reader.fieldnames:
                return [], ['ملف CSV فارغ أو لا يحتوي على ترويسة أعمدة.']
            normalized = [(name or '').strip().lstrip('\ufeff') for name in reader.fieldnames]
            missing = [header for header in self.headers if header not in normalized]
            if missing:
                errors.append('أعمدة مفقودة: ' + '، '.join(missing))
            for index, row in enumerate(reader, start=2):
                clean_row = {}
                for original, normalized_name in zip(reader.fieldnames, normalized):
                    clean_row[normalized_name] = (row.get(original) or '').strip()
                if any(clean_row.values()):
                    rows.append((index, clean_row))
        except csv.Error as exc:
            return [], [f'تعذر قراءة ملف CSV: {exc}']
        return rows, errors

    def validate_rows(self, rows):
        return [self.validate_row(row_number, raw) for row_number, raw in rows]

    def validate_row(self, row_number, raw):
        raise NotImplementedError

    def apply_mode_rules(self, result, matched):
        if result.errors:
            result.action = 'error'
            return result
        if self.mode == 'dry_run':
            result.action = 'update' if matched else 'create'
        elif self.mode == 'create_only':
            if matched:
                result.action = 'error'
                result.errors.append('يوجد سجل مطابق مسبقاً؛ وضع الإنشاء فقط لا يسمح بالتحديث.')
            else:
                result.action = 'create'
        elif self.mode == 'update_only':
            if matched:
                result.action = 'update'
            else:
                result.action = 'error'
                result.errors.append('لم يتم العثور على سجل مطابق للتحديث.')
        else:
            result.action = 'update' if matched else 'create'
        if matched:
            result.matched_id = matched.pk
        return result

    def save(self, results, user):
        created = updated = skipped = 0
        with transaction.atomic():
            for result in results:
                if result.action == 'create':
                    self.create_record(result, user)
                    created += 1
                elif result.action == 'update':
                    self.update_record(result, user)
                    updated += 1
                else:
                    skipped += 1
            ActivityLog.objects.create(
                actor=user,
                action='bulk_import',
                details={
                    'import_type': self.slug,
                    'mode': self.mode,
                    'created': created,
                    'updated': updated,
                    'skipped': skipped,
                },
            )
        return {'created': created, 'updated': updated, 'skipped': skipped}

    def create_record(self, result, user):
        raise NotImplementedError

    def update_record(self, result, user):
        raise NotImplementedError


def required(result, raw, field_name, label):
    value = (raw.get(field_name) or '').strip()
    if not value:
        result.errors.append(f'{label} مطلوب.')
    return value


def parse_bool(result, value, label, default=True):
    if value == '' or value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in BOOLEAN_TRUE:
        return True
    if normalized in BOOLEAN_FALSE:
        return False
    result.errors.append(f'{label} يجب أن يكون true/false أو yes/no أو 1/0 أو نعم/لا.')
    return default


def parse_int(result, value, label, required_field=False, min_value=0, positive=False):
    if value == '' or value is None:
        if required_field:
            result.errors.append(f'{label} مطلوب.')
        return None
    try:
        number = int(str(value).replace(',', '').strip())
    except (TypeError, ValueError):
        result.errors.append(f'{label} يجب أن يكون رقماً صحيحاً.')
        return None
    if positive and number <= 0:
        result.errors.append(f'{label} يجب أن يكون أكبر من صفر.')
    elif min_value is not None and number < min_value:
        result.errors.append(f'{label} لا يمكن أن يكون سالباً.')
    return number


def parse_dt(result, value, label, required_field=False):
    if not value:
        if required_field:
            result.errors.append(f'{label} مطلوب.')
        return None
    formats = ['%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d']
    for fmt in formats:
        try:
            parsed = datetime.strptime(value, fmt)
            if fmt == '%Y-%m-%d':
                parsed = parsed.replace(hour=0, minute=0)
            return timezone.make_aware(parsed, timezone.get_current_timezone()) if timezone.is_naive(parsed) else parsed
        except ValueError:
            continue
    result.errors.append(f'{label} تاريخ غير صالح. استخدم YYYY-MM-DD HH:MM.')
    return None


def parse_uuid(value):
    try:
        return uuid.UUID(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def split_values(value):
    return [part.strip() for part in (value or '').split(',') if part.strip()]


def choice_values(choices):
    return {value for value, _label in choices}


def unique_slug(base, model, field='code'):
    slug = slugify(base, allow_unicode=False) or 'imported'
    candidate = slug
    counter = 2
    while model.objects.filter(**{field: candidate}).exists():
        candidate = f'{slug}-{counter}'
        counter += 1
    return candidate


class ProductImporter(BaseImporter):
    slug = 'products'
    title = 'استيراد المنتجات'
    description = 'إضافة أو تحديث منتجات القائمة مع الأقسام والوسائط وخيارات المنتجات.'
    model_label = 'منتج'
    headers = ['code', 'name_ar', 'name_en', 'section', 'product_type', 'beverage_type', 'food_type', 'service_type', 'price_syp', 'description_ar', 'description_en', 'is_available', 'visible_on_qr', 'orderable_on_qr', 'sort_order', 'image_url', 'tags', 'modifier_groups', 'prep_station']
    sample = {'code': 'coffee-001', 'name_ar': 'قهوة عربية', 'name_en': 'Arabic Coffee', 'section': 'المشروبات الساخنة', 'product_type': 'beverage', 'beverage_type': 'coffee', 'price_syp': '25000', 'is_available': 'نعم', 'visible_on_qr': 'نعم', 'orderable_on_qr': 'نعم', 'sort_order': '10', 'tags': 'ساخن,قهوة', 'modifier_groups': 'حجم الكوب'}

    def validate_row(self, row_number, raw):
        result = RowResult(row_number, raw)
        name_ar = required(result, raw, 'name_ar', 'اسم المنتج بالعربية')
        product_type = required(result, raw, 'product_type', 'نوع المنتج')
        if product_type and product_type not in choice_values(Product.ItemType.choices):
            result.errors.append('نوع المنتج غير صالح. القيم المقبولة: ' + '، '.join(choice_values(Product.ItemType.choices)))
        beverage_type = raw.get('beverage_type', '')
        if beverage_type and beverage_type not in choice_values(Product.BeverageType.choices):
            result.errors.append('نوع المشروب غير صالح.')
        food_type = raw.get('food_type', '')
        if food_type and food_type not in choice_values(Product.FoodType.choices):
            result.errors.append('نوع الطعام غير صالح.')
        service_type = raw.get('service_type', '')
        if service_type and service_type not in choice_values(Product.ServiceType.choices):
            result.errors.append('نوع الخدمة غير صالح.')
        price = parse_int(result, raw.get('price_syp'), 'السعر', required_field=True, min_value=0)
        sort_order = parse_int(result, raw.get('sort_order'), 'ترتيب العرض', required_field=False, min_value=None) or 0
        section = self.find_section(raw.get('section', ''))
        category = self.find_category(raw.get('section', ''))
        prep_station = self.find_prep_station(raw.get('prep_station', ''))
        if raw.get('prep_station') and not prep_station:
            result.warnings.append('محطة التحضير غير موجودة وسيتم تجاهلها.')
        matched = self.find_match(raw, name_ar, section)
        tags, missing_tags = self.find_tags(raw.get('tags', ''))
        if missing_tags:
            result.warnings.append('وسوم غير موجودة ولن تُنشأ تلقائياً: ' + '، '.join(missing_tags))
        modifier_groups, invalid_groups, missing_groups = self.find_modifier_groups(raw.get('modifier_groups', ''), product_type, beverage_type, food_type, service_type)
        if missing_groups:
            result.warnings.append('مجموعات خيارات غير موجودة: ' + '، '.join(missing_groups))
        if invalid_groups:
            result.errors.append('مجموعات خيارات لا تنطبق على نوع المنتج: ' + '، '.join(invalid_groups))
        if not section and raw.get('section'):
            result.warnings.append('القسم غير موجود؛ سيتم إنشاؤه في أوضاع الإنشاء.')
        result.key_label = name_ar or raw.get('code', '') or f'سطر {row_number}'
        result.cleaned = {
            'code': raw.get('code', ''), 'name_ar': name_ar, 'name_en': raw.get('name_en', ''),
            'section_name': raw.get('section', ''), 'section': section, 'category': category,
            'item_type': product_type, 'beverage_type': beverage_type, 'food_type': food_type, 'service_type': service_type,
            'price_syp': price, 'description_ar': raw.get('description_ar', ''), 'description_en': raw.get('description_en', ''),
            'is_available': parse_bool(result, raw.get('is_available', ''), 'متاح', True),
            'visible_on_qr': parse_bool(result, raw.get('visible_on_qr', ''), 'ظاهر على QR', True),
            'orderable_on_qr': parse_bool(result, raw.get('orderable_on_qr', ''), 'قابل للطلب من QR', True),
            'sort_order': sort_order, 'image_url': raw.get('image_url', ''), 'tags': tags,
            'modifier_groups': modifier_groups, 'prep_station': prep_station,
        }
        return self.apply_mode_rules(result, matched)

    def find_section(self, name):
        if not name:
            return None
        return MenuSection.objects.filter(Q(name_ar__iexact=name) | Q(name_en__iexact=name)).first()

    def find_category(self, name):
        label = name or 'عام'
        return Category.objects.filter(Q(name_ar__iexact=label) | Q(name_en__iexact=label)).first()

    def find_prep_station(self, value):
        if not value:
            return None
        return PrepStation.objects.filter(Q(code__iexact=value) | Q(name_ar__iexact=value) | Q(name_en__iexact=value)).first()

    def find_match(self, raw, name_ar, section):
        code = raw.get('code', '')
        code_uuid = parse_uuid(code)
        if code_uuid:
            match = Product.objects.filter(public_code=code_uuid).first()
            if match:
                return match
        if code:
            match = Product.objects.filter(metadata__import_code=code).first()
            if match:
                return match
        if name_ar:
            query = Product.objects.filter(name_ar__iexact=name_ar)
            if section:
                query = query.filter(menu_sections=section)
            match = query.first()
            if match:
                return match
        return None

    def find_tags(self, value):
        found, missing = [], []
        for name in split_values(value):
            tag = Tag.objects.filter(Q(code__iexact=name) | Q(name_ar__iexact=name) | Q(name_en__iexact=name), is_active=True).first()
            (found if tag else missing).append(tag or name)
        return found, missing

    def find_modifier_groups(self, value, item_type, beverage_type, food_type, service_type):
        found, invalid, missing = [], [], []
        class Probe:
            pass
        probe = Probe()
        probe.item_type = item_type
        probe.beverage_type = beverage_type
        probe.food_type = food_type
        probe.service_type = service_type
        for name in split_values(value):
            group = ProductOptionGroup.objects.filter(Q(code__iexact=name) | Q(name_ar__iexact=name) | Q(name_en__iexact=name), is_active=True).first()
            if not group:
                missing.append(name)
            elif not group.applies_to_product(probe):
                invalid.append(name)
            else:
                found.append(group)
        return found, invalid, missing

    def ensure_related(self, cleaned):
        section = cleaned['section']
        category = cleaned['category']
        if not section and cleaned['section_name']:
            section = MenuSection.objects.create(name_ar=cleaned['section_name'])
        if not category:
            category = Category.objects.create(name_ar=cleaned['section_name'] or 'عام')
        cleaned['section'] = section
        cleaned['category'] = category
        return cleaned

    def assign_related(self, product, cleaned):
        if cleaned['section']:
            product.menu_sections.set([cleaned['section']])
        product.tags.set(cleaned['tags'])
        ProductOptionGroupAssignment.objects.filter(product=product).exclude(group__in=cleaned['modifier_groups']).update(is_active=False)
        for group in cleaned['modifier_groups']:
            ProductOptionGroupAssignment.objects.update_or_create(product=product, group=group, defaults={'is_active': True})
        if cleaned['image_url']:
            ProductMedia.objects.update_or_create(product=product, is_primary=True, is_active=True, defaults={'url': cleaned['image_url'], 'media_type': ProductMedia.MediaType.EXTERNAL_URL, 'alt_text_ar': product.name_ar})

    def record_data(self, cleaned):
        return {key: cleaned[key] for key in ['category', 'name_ar', 'name_en', 'description_ar', 'description_en', 'price_syp', 'is_available', 'sort_order', 'item_type', 'beverage_type', 'food_type', 'service_type', 'prep_station', 'visible_on_qr', 'orderable_on_qr'] if key != 'prep_station'} | {'prep_station_ref': cleaned['prep_station']}

    def create_record(self, result, user):
        cleaned = self.ensure_related(result.cleaned)
        product = Product.objects.create(**self.record_data(cleaned), metadata={'import_code': cleaned['code']} if cleaned['code'] else {})
        self.assign_related(product, cleaned)

    def update_record(self, result, user):
        product = Product.objects.get(pk=result.matched_id)
        cleaned = self.ensure_related(result.cleaned)
        for field_name, value in self.record_data(cleaned).items():
            setattr(product, field_name, value)
        metadata = dict(product.metadata or {})
        if cleaned['code']:
            metadata['import_code'] = cleaned['code']
        product.metadata = metadata
        product.save()
        self.assign_related(product, cleaned)


class EventImporter(BaseImporter):
    slug = 'events'; title = 'استيراد الفعاليات'; description = 'إضافة أو تحديث فعاليات وجدولها.'; model_label = 'فعالية'
    headers = ['code', 'title_ar', 'title_en', 'starts_at', 'ends_at', 'location', 'capacity', 'price_syp', 'description', 'status']
    sample = {'code': 'event-001', 'title_ar': 'أمسية موسيقية', 'starts_at': '2026-06-15 19:00', 'ends_at': '2026-06-15 21:00', 'capacity': '40', 'price_syp': '50000', 'status': 'published'}

    def validate_row(self, row_number, raw):
        result = RowResult(row_number, raw)
        title = required(result, raw, 'title_ar', 'عنوان الفعالية')
        starts_at = parse_dt(result, raw.get('starts_at'), 'وقت البداية', True)
        ends_at = parse_dt(result, raw.get('ends_at'), 'وقت النهاية')
        if starts_at and ends_at and ends_at <= starts_at:
            result.errors.append('وقت النهاية يجب أن يكون بعد وقت البداية.')
        capacity = parse_int(result, raw.get('capacity'), 'السعة', min_value=0)
        price = parse_int(result, raw.get('price_syp'), 'السعر', min_value=0)
        status = raw.get('status') or Event.Status.DRAFT
        if status not in choice_values(Event.Status.choices):
            result.errors.append('حالة الفعالية غير صالحة.')
        matched = self.find_match(raw, title, starts_at)
        if raw.get('price_syp'):
            result.warnings.append('لا يوجد حقل سعر مباشر للفعالية؛ سيتم إنشاء/تحديث نوع تذكرة أساسي بالسعر.')
        result.key_label = title
        result.cleaned = {'title_ar': title, 'title_en': raw.get('title_en', ''), 'starts_at': starts_at, 'ends_at': ends_at, 'capacity': capacity, 'price_syp': price, 'description_ar': raw.get('description', ''), 'status': status}
        return self.apply_mode_rules(result, matched)

    def find_match(self, raw, title, starts_at):
        code_uuid = parse_uuid(raw.get('code'))
        if code_uuid:
            match = Event.objects.filter(uuid=code_uuid).first()
            if match:
                return match
        if title and starts_at:
            return Event.objects.filter(title_ar__iexact=title, starts_at=starts_at).first()
        return None

    def upsert_ticket(self, event, cleaned):
        if cleaned['price_syp'] is not None:
            EventTicketType.objects.update_or_create(event=event, name_ar='تذكرة أساسية', defaults={'price_syp': cleaned['price_syp'], 'capacity': cleaned['capacity'], 'is_active': True})

    def create_record(self, result, user):
        event = Event.objects.create(title_ar=result.cleaned['title_ar'], title_en=result.cleaned['title_en'], starts_at=result.cleaned['starts_at'], ends_at=result.cleaned['ends_at'], capacity=result.cleaned['capacity'], description_ar=result.cleaned['description_ar'], status=result.cleaned['status'])
        self.upsert_ticket(event, result.cleaned)

    def update_record(self, result, user):
        event = Event.objects.get(pk=result.matched_id)
        for field_name in ['title_ar', 'title_en', 'starts_at', 'ends_at', 'capacity', 'description_ar', 'status']:
            setattr(event, field_name, result.cleaned[field_name])
        event.save()
        self.upsert_ticket(event, result.cleaned)


class VendorImporter(BaseImporter):
    slug = 'vendors'; title = 'استيراد الشركاء / vendors'; description = 'إضافة أو تحديث الشركاء والموردين.'; model_label = 'شريك'
    headers = ['code', 'name', 'contact_name', 'phone', 'email', 'category', 'notes', 'status']
    sample = {'code': 'vendor-001', 'name': 'مخبز الحارة', 'contact_name': 'أحمد', 'phone': '+963 900 000 000', 'email': 'hello@example.com', 'category': 'supplier', 'status': 'active'}

    def validate_row(self, row_number, raw):
        result = RowResult(row_number, raw)
        name = required(result, raw, 'name', 'اسم الشريك')
        email = raw.get('email', '')
        if email:
            try:
                validate_email(email)
            except ValidationError:
                result.warnings.append('البريد الإلكتروني غير صالح وسيتم تجاهله لأن نموذج الشركاء الحالي لا يحفظ البريد.')
        category = raw.get('category') or Vendor.VendorType.OTHER
        if category not in choice_values(Vendor.VendorType.choices):
            result.errors.append('تصنيف الشريك غير صالح.')
        status = raw.get('status', '').lower()
        is_active = False if status in {'inactive', 'disabled', '0', 'لا', 'false'} else True
        matched = self.find_match(raw, name)
        if not raw.get('phone'):
            result.warnings.append('لا يوجد هاتف؛ سيتم المطابقة بالاسم فقط وقد توجد تشابهات.')
        if raw.get('notes'):
            result.warnings.append('ملاحظات الشريك ستحفظ في settlement_notes حسب النموذج الحالي.')
        result.key_label = name
        result.cleaned = {'name_ar': name, 'contact_person': raw.get('contact_name', ''), 'phone': raw.get('phone', ''), 'vendor_type': category, 'settlement_notes': raw.get('notes', ''), 'is_active': is_active}
        return self.apply_mode_rules(result, matched)

    def find_match(self, raw, name):
        code_uuid = parse_uuid(raw.get('code'))
        if code_uuid:
            match = Vendor.objects.filter(uuid=code_uuid).first()
            if match:
                return match
        if name and raw.get('phone'):
            return Vendor.objects.filter(name_ar__iexact=name, phone__iexact=raw.get('phone')).first()
        if name:
            return Vendor.objects.filter(name_ar__iexact=name).first()
        return None

    def create_record(self, result, user):
        Vendor.objects.create(**result.cleaned)

    def update_record(self, result, user):
        vendor = Vendor.objects.get(pk=result.matched_id)
        for field_name, value in result.cleaned.items():
            setattr(vendor, field_name, value)
        vendor.save()


class MemberImporter(BaseImporter):
    slug = 'members'; title = 'استيراد الأعضاء'; description = 'إضافة أو تحديث الأعضاء وربط خطة العضوية والأرصدة الافتتاحية.'; model_label = 'عضو'
    headers = ['code', 'name', 'phone', 'email', 'membership_plan', 'starting_minutes', 'starting_credit_syp', 'notes']
    sample = {'code': 'member-001', 'name': 'سارة محمد', 'phone': '+963 944 000 000', 'email': 'sara@example.com', 'membership_plan': 'monthly', 'starting_minutes': '120', 'starting_credit_syp': '50000'}

    def validate_row(self, row_number, raw):
        result = RowResult(row_number, raw)
        name = required(result, raw, 'name', 'اسم العضو')
        if not raw.get('phone') and not raw.get('email') and not raw.get('code'):
            result.errors.append('يجب إدخال هاتف أو بريد أو كود للمطابقة.')
        minutes = parse_int(result, raw.get('starting_minutes'), 'الدقائق الافتتاحية', min_value=0)
        credit = parse_int(result, raw.get('starting_credit_syp'), 'الرصيد الافتتاحي', min_value=0)
        plan = self.find_plan(raw.get('membership_plan', ''))
        if raw.get('membership_plan') and not plan:
            result.errors.append('خطة العضوية غير موجودة.')
        if raw.get('email'):
            result.warnings.append('نموذج الأعضاء الحالي لا يحتوي حقل بريد؛ سيُستخدم البريد للمطابقة فقط إذا كان محفوظاً سابقاً غير متاح حالياً.')
        if raw.get('notes'):
            result.warnings.append('نموذج الأعضاء الحالي لا يحتوي حقل ملاحظات؛ سيتم تجاهل الملاحظات.')
        matched = self.find_match(raw)
        if not matched and self.mode in {'dry_run', 'create_only', 'create_and_update'} and not raw.get('phone'):
            result.errors.append('الهاتف مطلوب عند إنشاء عضو جديد لأن نموذج الأعضاء الحالي يعتمد عليه كمعرّف فريد.')
        result.key_label = name
        result.cleaned = {'name_ar': name, 'phone': raw.get('phone', ''), 'default_plan': plan, 'starting_minutes': minutes, 'starting_credit_syp': credit}
        return self.apply_mode_rules(result, matched)

    def find_plan(self, value):
        if not value:
            return None
        return MembershipPlan.objects.filter(Q(code__iexact=value) | Q(name_ar__iexact=value) | Q(name_en__iexact=value)).first()

    def find_match(self, raw):
        if raw.get('phone'):
            match = Member.objects.filter(phone__iexact=raw.get('phone')).first()
            if match:
                return match
        code_uuid = parse_uuid(raw.get('code'))
        if code_uuid:
            return Member.objects.filter(public_code=code_uuid).first()
        return None

    def create_record(self, result, user):
        member = Member.objects.create(name_ar=result.cleaned['name_ar'], phone=result.cleaned['phone'], default_plan=result.cleaned['default_plan'], balance_syp=result.cleaned['starting_credit_syp'] or 0)
        self.create_ledger(member, result.cleaned, user)

    def update_record(self, result, user):
        member = Member.objects.get(pk=result.matched_id)
        member.name_ar = result.cleaned['name_ar']
        if result.cleaned['phone']:
            member.phone = result.cleaned['phone']
        member.default_plan = result.cleaned['default_plan']
        if result.cleaned['starting_credit_syp'] is not None and self.mode in {'update_only', 'create_and_update'}:
            member.balance_syp = result.cleaned['starting_credit_syp']
        member.save()
        self.create_ledger(member, result.cleaned, user)

    def create_ledger(self, member, cleaned, user):
        subscription = None
        if cleaned['default_plan'] and cleaned['starting_minutes'] is not None:
            subscription = MembershipSubscription.objects.create(member=member, plan=cleaned['default_plan'], starts_at=timezone.now(), remaining_internet_minutes=cleaned['starting_minutes'], remaining_credit_syp=cleaned['starting_credit_syp'], notes='تم إنشاؤه عبر الاستيراد الجماعي')
        if cleaned['starting_minutes']:
            MemberCreditLedger.objects.create(member=member, subscription=subscription, change_type='add_minutes', minutes_delta=cleaned['starting_minutes'], created_by=user, notes='رصيد افتتاحي من الاستيراد الجماعي')
        if cleaned['starting_credit_syp']:
            MemberCreditLedger.objects.create(member=member, subscription=subscription, change_type='add_credit', credit_delta_syp=cleaned['starting_credit_syp'], created_by=user, notes='رصيد افتتاحي من الاستيراد الجماعي')


class InternetPackageImporter(BaseImporter):
    slug = 'internet-packages'; title = 'استيراد باقات الإنترنت'; description = 'إضافة أو تحديث باقات الإنترنت الحالية.'; model_label = 'باقة إنترنت'
    headers = ['code', 'name_ar', 'name_en', 'minutes', 'price_syp', 'is_active']
    sample = {'code': 'net-60', 'name_ar': 'ساعة إنترنت', 'name_en': '60 minutes', 'minutes': '60', 'price_syp': '20000', 'is_active': 'نعم'}

    def validate_row(self, row_number, raw):
        result = RowResult(row_number, raw)
        name = required(result, raw, 'name_ar', 'اسم الباقة')
        minutes = parse_int(result, raw.get('minutes'), 'الدقائق', True, positive=True)
        price = parse_int(result, raw.get('price_syp'), 'السعر', True, min_value=0)
        is_active = parse_bool(result, raw.get('is_active'), 'فعال', True)
        if is_active is False:
            result.warnings.append('نموذج باقات الإنترنت الحالي لا يحتوي حقل is_active؛ سيتم تجاهل القيمة.')
        matched = self.find_match(raw, name)
        result.key_label = name
        result.cleaned = {'name_ar': name, 'name_en': raw.get('name_en', ''), 'duration_minutes': minutes, 'price_syp': price}
        return self.apply_mode_rules(result, matched)

    def find_match(self, raw, name):
        code_uuid = parse_uuid(raw.get('code'))
        if code_uuid:
            match = InternetPackage.objects.filter(public_code=code_uuid).first()
            if match:
                return match
        if name:
            return InternetPackage.objects.filter(name_ar__iexact=name).first()
        return None

    def create_record(self, result, user):
        InternetPackage.objects.create(**result.cleaned)

    def update_record(self, result, user):
        package = InternetPackage.objects.get(pk=result.matched_id)
        for field_name, value in result.cleaned.items():
            setattr(package, field_name, value)
        package.save()


class TableImporter(BaseImporter):
    slug = 'tables'; title = 'استيراد الطاولات / QR'; description = 'إضافة أو تحديث الطاولات وروابط QR بدون كشف أرقام قاعدة البيانات.'; model_label = 'طاولة'
    headers = ['code', 'name_ar', 'room_or_area', 'qr_token', 'is_active', 'notes']
    sample = {'code': 'table-01', 'name_ar': 'طاولة 1', 'room_or_area': 'الصالة', 'qr_token': '', 'is_active': 'نعم', 'notes': 'قرب النافذة'}

    def validate_row(self, row_number, raw):
        result = RowResult(row_number, raw)
        name = required(result, raw, 'name_ar', 'اسم الطاولة')
        qr_token = parse_uuid(raw.get('qr_token'))
        if raw.get('qr_token') and not qr_token:
            result.errors.append('رمز QR يجب أن يكون UUID صالحاً أو اتركه فارغاً ليتم توليده.')
        is_active = parse_bool(result, raw.get('is_active'), 'فعال', True)
        if is_active is False:
            result.warnings.append('نموذج الطاولات الحالي لا يحتوي حقل is_active؛ سيتم تجاهل القيمة.')
        if raw.get('notes'):
            result.warnings.append('نموذج الطاولات الحالي لا يحتوي حقل ملاحظات؛ سيتم تجاهل الملاحظات.')
        matched = self.find_match(raw, name)
        result.key_label = name
        result.cleaned = {'name_ar': name, 'room_name': raw.get('room_or_area') or 'عام', 'qr_token': qr_token}
        return self.apply_mode_rules(result, matched)

    def find_match(self, raw, name):
        qr_token = parse_uuid(raw.get('qr_token'))
        if qr_token:
            match = TableArea.objects.filter(qr_token=qr_token).first()
            if match:
                return match
        if name:
            return TableArea.objects.filter(name_ar__iexact=name, room__name_ar__iexact=raw.get('room_or_area') or 'عام').first()
        return None

    def room(self, name):
        room, _created = Room.objects.get_or_create(name_ar=name or 'عام')
        return room

    def create_record(self, result, user):
        kwargs = {'name_ar': result.cleaned['name_ar'], 'room': self.room(result.cleaned['room_name'])}
        if result.cleaned['qr_token']:
            kwargs['qr_token'] = result.cleaned['qr_token']
        TableArea.objects.create(**kwargs)

    def update_record(self, result, user):
        table = TableArea.objects.get(pk=result.matched_id)
        table.name_ar = result.cleaned['name_ar']
        table.room = self.room(result.cleaned['room_name'])
        if result.cleaned['qr_token']:
            table.qr_token = result.cleaned['qr_token']
        table.save()


IMPORTERS = {cls.slug: cls for cls in [ProductImporter, EventImporter, VendorImporter, MemberImporter, InternetPackageImporter, TableImporter]}


def admin_required(request):
    user = request.user
    if not (user.is_superuser or getattr(user, 'role', '') == 'admin'):
        ActivityLog.objects.create(actor=user if user.is_authenticated else None, action='staff_import_access_denied', details={'user_id': getattr(user, 'pk', None), 'role': getattr(user, 'role', '')})
        messages.error(request, 'هذه الصفحة مخصصة للمدير فقط. لا تملك صلاحية الاستيراد الجماعي.')
        return False
    return True


def get_importer_or_404(import_type, mode='dry_run'):
    importer_cls = IMPORTERS.get(import_type)
    if not importer_cls:
        raise Http404('نوع الاستيراد غير موجود')
    return importer_cls(mode=mode)


def session_key(import_type):
    return f'bulk_import_{import_type}'


@login_required
def staff_import_home(request):
    if not admin_required(request):
        return redirect('staff_home')
    return render(request, 'staff/import_home.html', {'importers': [cls() for cls in IMPORTERS.values()]})


@login_required
def staff_import_upload(request, import_type):
    if not admin_required(request):
        return redirect('staff_home')
    importer = get_importer_or_404(import_type)
    return render(request, 'staff/import_upload.html', {'importer': importer, 'modes': IMPORT_MODES})


@login_required
def staff_import_template(request, import_type):
    if not admin_required(request):
        return redirect('staff_home')
    return get_importer_or_404(import_type).template_response()


@login_required
def staff_import_preview(request, import_type):
    if not admin_required(request):
        return redirect('staff_home')
    if request.method != 'POST':
        return redirect('staff_import_upload', import_type=import_type)
    mode = request.POST.get('mode', 'dry_run')
    importer = get_importer_or_404(import_type, mode)
    uploaded = request.FILES.get('csv_file')
    if not uploaded:
        messages.error(request, 'اختر ملف CSV أولاً.')
        return redirect('staff_import_upload', import_type=import_type)
    if uploaded.size > 512 * 1024:
        messages.error(request, 'حجم الملف كبير لهذه المرحلة. الرجاء تقسيمه إلى ملفات أصغر من 512KB.')
        return redirect('staff_import_upload', import_type=import_type)
    try:
        csv_text = uploaded.read().decode('utf-8-sig')
    except UnicodeDecodeError:
        messages.error(request, 'تعذر قراءة الملف. الرجاء استخدام CSV بترميز UTF-8.')
        return redirect('staff_import_upload', import_type=import_type)
    rows, file_errors = importer.parse_csv(csv_text)
    results = importer.validate_rows(rows) if not file_errors else []
    request.session[session_key(import_type)] = {'csv_text': csv_text, 'mode': mode}
    request.session.modified = True
    has_errors = bool(file_errors or any(row.errors for row in results))
    has_warnings = any(row.warnings for row in results)
    return render(request, 'staff/import_preview.html', {'importer': importer, 'modes': IMPORT_MODES, 'results': results, 'file_errors': file_errors, 'has_errors': has_errors, 'has_warnings': has_warnings, 'mode': mode})


@login_required
def staff_import_confirm(request, import_type):
    if not admin_required(request):
        return redirect('staff_home')
    if request.method != 'POST':
        return redirect('staff_import_upload', import_type=import_type)
    stored = request.session.get(session_key(import_type))
    if not stored:
        messages.error(request, 'انتهت جلسة المعاينة. أعد رفع الملف.')
        return redirect('staff_import_upload', import_type=import_type)
    importer = get_importer_or_404(import_type, stored.get('mode', 'dry_run'))
    rows, file_errors = importer.parse_csv(stored.get('csv_text', ''))
    results = importer.validate_rows(rows) if not file_errors else []
    has_errors = bool(file_errors or any(row.errors for row in results))
    has_warnings = any(row.warnings for row in results)
    if has_errors:
        messages.error(request, 'لا يمكن الحفظ بسبب وجود أخطاء في الملف.')
        return render(request, 'staff/import_preview.html', {'importer': importer, 'modes': IMPORT_MODES, 'results': results, 'file_errors': file_errors, 'has_errors': True, 'has_warnings': has_warnings, 'mode': importer.mode})
    if has_warnings and request.POST.get('confirm_warnings') != 'yes':
        messages.error(request, 'توجد تحذيرات. فعّل خيار تأكيد المتابعة مع التحذيرات قبل الحفظ.')
        return render(request, 'staff/import_preview.html', {'importer': importer, 'modes': IMPORT_MODES, 'results': results, 'file_errors': file_errors, 'has_errors': False, 'has_warnings': has_warnings, 'mode': importer.mode})
    if importer.mode == 'dry_run':
        summary = {'created': 0, 'updated': 0, 'skipped': len(results)}
        messages.info(request, 'تم تنفيذ معاينة فقط بدون حفظ أي تغييرات.')
    else:
        summary = importer.save(results, request.user)
        messages.success(request, 'تم تنفيذ الاستيراد الجماعي بنجاح.')
    request.session.pop(session_key(import_type), None)
    return render(request, 'staff/import_result.html', {'importer': importer, 'summary': summary, 'results': results, 'mode': importer.mode})
