"""Privacy-safe, preview-first, all-or-nothing event booking CSV imports.

No revenue, payments, visits, messages or products are created by imports.
"""
import csv
import hashlib
import hmac
import io
import json
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.permissions import user_has_capability
from core.models import ActivityLog
from events.models import Event, EventTicketType
from .models import Reservation, ReservationImportBatch
from .services import create_reservation, event_reserved_party_size
from .ticketing import set_event_ticket_allocations


MAX_FILE_BYTES = 512_000
MAX_ROWS = 250
PHONE_RE = re.compile(r'^\+?[0-9]{7,16}$')
ARABIC_DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789')
ACTIVE = (Reservation.Status.PENDING, Reservation.Status.CONFIRMED)


def normalize_phone(value):
    cleaned = str(value or '').translate(ARABIC_DIGITS)
    cleaned = re.sub(r'[\s()\-]', '', cleaned)
    if not PHONE_RE.fullmatch(cleaned):
        raise ValidationError('رقم الهاتف غير صالح (7–16 رقماً مع + اختياري).')
    return cleaned


def _fingerprint(event_id, rows):
    data = json.dumps({'event': event_id, 'rows': rows}, ensure_ascii=False,
                      sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hmac.new(settings.SECRET_KEY.encode('utf-8'), data, hashlib.sha256).hexdigest()


def validate_rows(event_id, rows, *, actor=None):
    """Return canonical import rows and HMAC. No writes or customer data logging."""
    if not isinstance(rows, list) or not rows or len(rows) > MAX_ROWS:
        raise ValidationError('عدد صفوف الحجز يجب أن يكون بين 1 و250.')
    event = Event.objects.get(pk=event_id)
    tiers = {tier.pk: tier for tier in EventTicketType.objects.filter(
        event=event, is_active=True,
    )}
    if not tiers:
        raise ValidationError('الفعالية لا تحتوي على فئات تذاكر فعّالة.')
    canonical = []
    seen = set()
    for number, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValidationError(f'السطر {number}: صيغة الحجز غير صالحة.')
        name = str(row.get('name') or '').strip()
        if (not name or len(name) > 120 or name.startswith(('=', '+', '-', '@'))
                or any(ord(ch) < 32 for ch in name)):
            raise ValidationError(f'السطر {number}: اسم صاحب الحجز غير صالح.')
        try:
            phone = normalize_phone(row.get('phone'))
        except ValidationError as error:
            raise ValidationError(f'السطر {number}: {error.messages[0]}')
        if phone in seen:
            raise ValidationError(f'السطر {number}: تكرار رقم هاتف في الملف؛ افحص الحجوزات يدوياً.')
        seen.add(phone)
        raw_size = row.get('party_size')
        if isinstance(raw_size, bool) or str(raw_size).strip() not in [str(i) for i in range(1, 101)]:
            raise ValidationError(f'السطر {number}: عدد الضيوف يجب أن يكون بين 1 و100.')
        size = int(raw_size)
        status = str(row.get('status') or '').strip()
        if status not in ACTIVE:
            raise ValidationError(f'السطر {number}: الحالة يجب أن تكون pending أو confirmed.')
        values = row.get('tiers')
        if not isinstance(values, dict):
            raise ValidationError(f'السطر {number}: فئات التذاكر مفقودة.')
        normalized_tiers = {}
        for key, quantity in values.items():
            try:
                tid = int(key)
                n = int(quantity)
            except (TypeError, ValueError, OverflowError):
                raise ValidationError(f'السطر {number}: عدد فئة التذاكر غير صالح.')
            if tid not in tiers or isinstance(quantity, bool) or str(quantity).strip() != str(n) or n < 0:
                raise ValidationError(f'السطر {number}: فئة تذاكر غير موجودة أو عدد غير صالح.')
            if n:
                normalized_tiers[str(tid)] = n
        if sum(normalized_tiers.values()) != size:
            raise ValidationError(f'السطر {number}: مجموع فئات التذاكر لا يطابق عدد الضيوف.')
        canonical.append({
            'name': name, 'phone': phone, 'party_size': size,
            'status': status, 'tiers': normalized_tiers,
        })
    return canonical, _fingerprint(event_id, canonical)


def preview_uploaded_csv(event_id, uploaded):
    if not uploaded:
        raise ValidationError('اختر ملف CSV.')
    raw = uploaded.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValidationError('الملف كبير جداً. الحد 512 كيلوبايت.')
    try:
        data = raw.decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(data, newline=''), strict=True)
        headers = reader.fieldnames or []
        if len(headers) != len(set(headers)):
            raise ValidationError('توجد عناوين أعمدة مكررة.')
        allowed = {'name', 'phone', 'party_size', 'status'}
        tiers = set(EventTicketType.objects.filter(
            event_id=event_id, is_active=True,
        ).values_list('pk', flat=True))
        ticket_headers = {f'ticket_{tid}' for tid in tiers}
        extra = set(headers) - allowed - ticket_headers
        if extra or not allowed.issubset(headers) or not (set(headers) & ticket_headers):
            raise ValidationError('أعمدة CSV غير مطابقة؛ استخدم قالب التذاكر الخاص بهذه الفعالية.')
        rows = []
        for i, raw_row in enumerate(reader, 1):
            if i > MAX_ROWS:
                raise ValidationError('الحد الأقصى للملف 250 حجزاً.')
            if None in raw_row:
                raise ValidationError(f'السطر {i}: عدد الأعمدة غير متطابق.')
            parsed = {key: raw_row.get(key) for key in allowed}
            parsed['tiers'] = {
                key[7:]: (raw_row.get(key) or '0').strip() for key in ticket_headers & set(headers)
            }
            rows.append(parsed)
    except (UnicodeDecodeError, csv.Error) as error:
        raise ValidationError('تعذر قراءة CSV بترميز UTF-8 أو أن تنسيق الملف غير صالح.') from error
    return validate_rows(event_id, rows)


@transaction.atomic
def import_event_bookings(event_id, rows, fingerprint, *, actor):
    if not getattr(actor, 'is_active', False) or not user_has_capability(actor, 'reservations'):
        raise ValidationError('لا تملك صلاحية استيراد حجوزات الفعاليات.')
    canonical, actual = validate_rows(event_id, rows)
    if not hmac.compare_digest(str(fingerprint), actual):
        raise ValidationError('بيانات الاستيراد تغيرت؛ أعد المعاينة.')
    event = Event.objects.select_for_update().get(pk=event_id)
    if event.performances.count() != 1:
        raise ValidationError({'event': 'استيراد الحجوزات الجماعي لفعالية متعددة العروض يتطلب تحديد العرض أولاً؛ استخدم الحجز الفردي حالياً.'})
    if event.status in {Event.Status.CANCELLED, Event.Status.COMPLETED} or event.starts_at <= timezone.now():
        raise ValidationError('الفعالية بدأت أو انتهت ولا تسمح باستيراد حجوزات جديدة.')
    previous = ReservationImportBatch.objects.filter(
        event=event, fingerprint=actual,
    ).first()
    if previous:
        return previous, False
    duplicates = set()
    for phone in Reservation.objects.filter(
        event=event, status__in=ACTIVE,
    ).values_list('phone', flat=True):
        try:
            duplicates.add(normalize_phone(phone))
        except ValidationError:
            # Legacy malformed contacts remain protected from blind updates,
            # but cannot be used as canonical duplicate keys.
            pass
    if duplicates.intersection(row['phone'] for row in canonical):
        raise ValidationError('توجد أرقام هواتف محجوزة بالفعل في هذه الفعالية؛ راجع التكرار يدوياً.')
    needed = sum(row['party_size'] for row in canonical)
    if event.capacity is not None and event_reserved_party_size(event.pk) + needed > event.capacity:
        raise ValidationError('عدد الضيوف في ملف الاستيراد يتجاوز السعة المتبقية للفعالية.')
    batch = ReservationImportBatch.objects.create(
        event=event, fingerprint=actual, imported_count=len(canonical),
        created_by=actor,
    )
    for row in canonical:
        booking = create_reservation(Reservation(
            reservation_type=Reservation.ReservationType.EVENT,
            event=event, name=row['name'], phone=row['phone'],
            party_size=row['party_size'], status=row['status'],
            created_by=actor,
        ))
        set_event_ticket_allocations(booking.pk, row['tiers'], actor=actor)
    ActivityLog.objects.create(
        actor=actor, action='reservation.event_import.completed',
        details={'event_id': event.pk, 'batch_id': batch.pk, 'count': len(canonical)},
    )
    return batch, True
