"""Event requests never hold capacity or collect money; only staff approval reserves seats."""
import hashlib
import re
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.utils import timezone

from accounts.permissions import user_has_capability
from core.models import AuditEvent
from events.models import Event
from reservations.models import EventAttendanceRequest as Request, Reservation
from reservations.services import create_reservation


class RequestRateLimited(Exception):
    pass


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def normalize_phone(value):
    value = value.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789'))
    value = ''.join(char for char in value if char not in ' ()-')
    if len(value) > 30 or not re.fullmatch(r'\+?[0-9]{5,30}', value):
        raise ValidationError('أدخل رقم تواصل صالحاً.')
    return value


def accepting_events(now=None):
    return Event.objects.filter(status='published', is_public=True, accepts_attendance_requests=True,
                                starts_at__gt=now or timezone.now(), capacity__gt=0)


def _audit(row, action, actor=None, reason=''):
    AuditEvent.objects.create(actor=actor, action=action, source=row,
                              after_snapshot={'status': row.status, 'reservation_id': row.reservation_id, 'reason': reason},
                              channel='staff' if actor else 'public-attendance')


def _submission_locks(owner_hash, ip_hash):
    # Transaction-scoped locks serialize rate counters across browsers/events.
    # Sort keys consistently when two hashes map to different lock buckets.
    keys = sorted({int(h[:16], 16) - 2**63 for h in (owner_hash, ip_hash)})
    with connection.cursor() as cursor:
        for key in keys:
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [key])


@transaction.atomic
def submit_request(*, event_uuid, owner_hash, ip_hash, client_key, name, phone, party_size):
    name, phone = name.strip(), normalize_phone(phone.strip())
    _submission_locks(owner_hash, ip_hash)
    existing = Request.objects.filter(client_key=client_key).first()
    if existing:
        if existing.owner_hash != owner_hash:
            raise PermissionDenied
        if (existing.name, existing.phone, existing.party_size) != (name, phone, party_size) or existing.event_uuid != event_uuid:
            raise ValidationError('هذا الطلب أُرسل ببيانات مختلفة. افتح طلباً جديداً.')
        return existing
    now = timezone.now()
    since = now-timedelta(hours=1)
    if (Request.objects.filter(owner_hash=owner_hash, created_at__gte=since).count() >= settings.PUBLIC_ATTENDANCE_OWNER_HOURLY_LIMIT
            or Request.objects.filter(ip_hash=ip_hash, created_at__gte=since).count() >= settings.PUBLIC_ATTENDANCE_IP_HOURLY_LIMIT):
        raise RequestRateLimited
    event = accepting_events(now).select_for_update().filter(uuid=event_uuid).first()
    if event is None:
        raise ValidationError('طلبات الحضور غير متاحة لهذه الفعالية.')
    if not name.strip() or not phone.strip() or len(name) > 120 or len(phone) > 30:
        raise ValidationError('الاسم ورقم التواصل مطلوبان وبطول صالح.')
    if isinstance(party_size, bool) or not isinstance(party_size, int) or party_size < 1 or party_size > min(event.capacity, settings.PUBLIC_ATTENDANCE_MAX_PARTY_SIZE):
        raise ValidationError('عدد الأشخاص المطلوب غير متاح.')
    active = Request.objects.filter(event=event, owner_hash=owner_hash, status__in=['pending', 'approved']).first()
    if active:
        return active
    row = Request(event=event, event_uuid=event.uuid, event_title=event.title_ar, client_key=client_key, owner_hash=owner_hash,
                  ip_hash=ip_hash, name=name, phone=phone, party_size=party_size)
    row.full_clean()
    row.save()
    _audit(row, 'event_attendance_requested')
    return row


@transaction.atomic
def withdraw_request(request_uuid, *, owner_hash):
    row = Request.objects.select_for_update().get(uuid=request_uuid, owner_hash=owner_hash)
    if row.status == Request.Status.WITHDRAWN:
        return row
    if row.status != Request.Status.PENDING:
        raise ValidationError('تعديل الطلب بعد اعتماده يتم عبر الفريق.')
    row.status = Request.Status.WITHDRAWN
    row.save(update_fields=['status', 'updated_at'])
    _audit(row, 'event_attendance_withdrawn')
    return row


@transaction.atomic
def review_request(request_uuid, *, actor, action, reason=''):
    if not user_has_capability(actor, 'reservations'):
        raise PermissionDenied
    row = Request.objects.select_for_update().get(uuid=request_uuid)
    if action not in {'approve', 'reject'}:
        raise ValidationError('الإجراء غير صالح.')
    if row.status == Request.Status.APPROVED and action == 'approve':
        return row  # Idempotent: never create a second reservation.
    if row.status == Request.Status.REJECTED and action == 'reject':
        return row
    if row.status != Request.Status.PENDING:
        raise ValidationError('تمت معالجة الطلب مسبقاً.')
    if action == 'approve':
        event = accepting_events().select_for_update().filter(pk=row.event_id).first()
        if event is None:
            raise ValidationError('الفعالية لم تعد تقبل الطلبات. راجع الحالة قبل اعتماد الحضور.')
        row.reservation = create_reservation(Reservation(
            reservation_type='event', event=event, name=row.name, phone=row.phone,
            party_size=row.party_size, status='confirmed', deposit_syp=0, created_by=actor))
        row.status = Request.Status.APPROVED
    else:
        if not reason.strip() or len(reason) > 300:
            raise ValidationError('سبب الرفض مطلوب، حتى 300 حرف.')
        row.status = Request.Status.REJECTED
        row.review_reason = reason.strip()
    row.reviewed_by = actor
    row.reviewed_at = timezone.now()
    row.save(update_fields=['reservation', 'status', 'review_reason', 'reviewed_by', 'reviewed_at', 'updated_at'])
    _audit(row, 'event_attendance_'+action, actor, row.review_reason)
    return row
