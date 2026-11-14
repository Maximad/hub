"""Event requests never hold capacity or collect money; only staff approval reserves seats."""
import hashlib
import re
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from accounts.permissions import user_has_capability
from core.models import AuditEvent
from events.models import Event, EventPerformance
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
    now = now or timezone.now()
    all_shows = EventPerformance.objects.filter(event_id=OuterRef('pk'))
    upcoming = all_shows.filter(status=EventPerformance.Status.SCHEDULED, starts_at__gt=now)
    return Event.objects.filter(
        status='published', is_public=True, accepts_attendance_requests=True, capacity__gt=0,
    ).annotate(has_shows=Exists(all_shows), has_future_show=Exists(upcoming)).filter(
        Q(has_future_show=True) | Q(has_shows=False, starts_at__gt=now)
    )


def select_request_performance(event, performance_uuid=None):
    """Resolve a particular future show; missing selection is unsafe for multi-show events."""
    if event is None:
        raise ValidationError('الفعالية غير متاحة.')
    now = timezone.now()
    queryset = EventPerformance.objects.filter(
        event=event, status=EventPerformance.Status.SCHEDULED, starts_at__gt=now)
    if performance_uuid:
        selected = queryset.filter(uuid=performance_uuid).first()
        if selected is None:
            raise ValidationError('موعد العرض المختار غير متاح.')
        return selected
    rows = list(queryset.order_by('starts_at')[:2])
    if len(rows) > 1:
        raise ValidationError('اختر موعد العرض قبل إرسال الطلب.')
    if rows:
        return rows[0]
    if EventPerformance.objects.filter(event=event).exists() or event.starts_at <= now:
        raise ValidationError('لا توجد مواعيد متاحة للحضور.')
    return None


def _audit(row, action, actor=None, reason=''):
    AuditEvent.objects.create(actor=actor, action=action, source=row,
                              after_snapshot={'status': row.status, 'reservation_id': row.reservation_id, 'reason': reason},
                              channel='staff' if actor else 'public-attendance')


def _submission_locks(owner_hash, ip_hash, client_key):
    # Preserve existing rate-counter locks and serialize the globally unique
    # retry key even when callers share neither browser, IP nor event.
    hashes = (owner_hash, ip_hash, digest('attendance-client:'+str(client_key)))
    keys = sorted({int(h[:16], 16) - 2**63 for h in hashes})
    with connection.cursor() as cursor:
        for key in keys:
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [key])


def _lock_accepting_event(**lookup):
    event = accepting_events().select_for_update().filter(**lookup).first()
    # The SQL predicate's timestamp was captured before a possible lock wait.
    # Recheck the clock once the event is locked, before any write is allowed.
    if event is None:
        return None
    # Repeat the time gate after the row-lock wait; do not accept an event
    # whose last performance started while the transaction was blocked.
    if not EventPerformance.objects.filter(
            event=event, status=EventPerformance.Status.SCHEDULED,
            starts_at__gt=timezone.now()).exists() and (
            EventPerformance.objects.filter(event=event).exists()
            or event.starts_at <= timezone.now()):
        return None
    return event


@transaction.atomic
def submit_request(*, event_uuid, owner_hash, ip_hash, client_key, name, phone, party_size,
                   performance_uuid=None):
    name, phone = name.strip(), normalize_phone(phone.strip())
    _submission_locks(owner_hash, ip_hash, client_key)
    existing = Request.objects.filter(client_key=client_key).first()
    if existing:
        if existing.owner_hash != owner_hash:
            raise PermissionDenied
        if ((existing.name, existing.phone, existing.party_size) != (name, phone, party_size)
                or existing.event_uuid != event_uuid
                or (performance_uuid and str(existing.performance_uuid) != str(performance_uuid))):
            raise ValidationError('هذا الطلب أُرسل ببيانات مختلفة. افتح طلباً جديداً.')
        return existing
    # Resolve the performance before deduplication: the same browser may request
    # two separate shows of one event, but never duplicate one active request.
    preliminary = Event.objects.filter(uuid=event_uuid).first()
    performance = select_request_performance(preliminary, performance_uuid)
    active = Request.objects.filter(event_uuid=event_uuid, owner_hash=owner_hash,
                                    performance_uuid=performance.uuid if performance else None,
                                    status__in=['pending', 'approved']).first()
    if active:
        return active
    now = timezone.now()
    since = now-timedelta(hours=1)
    if (Request.objects.filter(owner_hash=owner_hash, created_at__gte=since).count() >= settings.PUBLIC_ATTENDANCE_OWNER_HOURLY_LIMIT
            or Request.objects.filter(ip_hash=ip_hash, created_at__gte=since).count() >= settings.PUBLIC_ATTENDANCE_IP_HOURLY_LIMIT):
        raise RequestRateLimited
    event = _lock_accepting_event(uuid=event_uuid)
    if event is None:
        raise ValidationError('طلبات الحضور غير متاحة لهذه الفعالية.')
    # Revalidate after the event lock: staff could have cancelled a show.
    performance = select_request_performance(event, performance.uuid if performance else None)
    if not name.strip() or not phone.strip() or len(name) > 120 or len(phone) > 30:
        raise ValidationError('الاسم ورقم التواصل مطلوبان وبطول صالح.')
    capacity = performance.effective_capacity if performance else event.capacity
    if isinstance(party_size, bool) or not isinstance(party_size, int) or party_size < 1 or party_size > min(capacity, settings.PUBLIC_ATTENDANCE_MAX_PARTY_SIZE):
        raise ValidationError('عدد الأشخاص المطلوب غير متاح.')
    row = Request(event=event, event_uuid=event.uuid, event_title=event.title_ar,
                  performance=performance, performance_uuid=performance.uuid if performance else None,
                  client_key=client_key, owner_hash=owner_hash,
                  ip_hash=ip_hash, name=name, phone=phone, party_size=party_size)
    row.full_clean()
    row.save()
    _audit(row, 'event_attendance_requested')
    return row


def attendance_access(owner_hash=None, account_id=None):
    """Claimed requests require the account; browser possession only grants unclaimed rows."""
    access = Q(pk__in=[])
    if owner_hash:
        access |= Q(owner_hash=owner_hash, account__isnull=True)
    if account_id:
        access |= Q(account_id=account_id)
    return access


@transaction.atomic
def claim_request(request_uuid, *, owner_hash, account_id):
    if not owner_hash or not account_id:
        raise PermissionDenied
    row = Request.objects.select_for_update().get(uuid=request_uuid, owner_hash=owner_hash)
    if row.account_id == account_id:
        return row
    if row.account_id is not None:
        raise PermissionDenied
    row.account_id = account_id
    row.save(update_fields=['account', 'updated_at'])
    _audit(row, 'event_attendance_account_claimed')
    return row


@transaction.atomic
def withdraw_request(request_uuid, *, owner_hash=None, account_id=None):
    row = Request.objects.select_for_update().filter(attendance_access(owner_hash, account_id)).get(uuid=request_uuid)
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
        event = _lock_accepting_event(pk=row.event_id)
        if event is None:
            raise ValidationError('الفعالية لم تعد تقبل الطلبات. راجع الحالة قبل اعتماد الحضور.')
        performance = select_request_performance(event, row.performance_uuid)
        row.reservation = create_reservation(Reservation(
            reservation_type='event', event=event, performance=performance, name=row.name, phone=row.phone,
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
