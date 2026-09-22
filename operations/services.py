import base64
import hashlib
import secrets
from datetime import datetime, time, timedelta

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import (
    ActivityLog,
    DailyClose,
    FinancialAccount,
    HubVisit,
    InternetNetworkOperation,
    InternetSession,
    Order,
    Payment,
    Shift,
)
from core.services.internet_access import end_usage_session
from core.services.network_operations import enqueue_network_operation
from core.services.visit_internet import finalize_visit_metered_session
from internet.models import InternetSessionNetworkOperation
from internet.session_network_operations import enqueue_session_network_operation

from .models import BusinessDay, BusinessDayException, StaffDailyCodeReceipt


TERMINAL_ORDER_STATUSES = {Order.Status.SERVED, Order.Status.CANCELLED}
STAFF_CODE_DIGITS = 6


def current_business_date(at=None):
    at = timezone.localtime(at or timezone.now())
    cutoff_hour = int(getattr(settings, 'BUSINESS_DAY_CUTOFF_HOUR', 4))
    if at.hour < cutoff_hour:
        return (at - timedelta(days=1)).date()
    return at.date()


def business_day_bounds(day):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(
        datetime.combine(day.business_date, time(hour=int(day.cutoff_hour))),
        timezone=tz,
    )
    return start, start + timedelta(days=1)


def _cipher():
    key = hashlib.sha256(settings.SECRET_KEY.encode('utf-8')).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _staff_users():
    User = get_user_model()
    provider_role = getattr(User.Role, 'INTERNET_PROVIDER', 'internet_provider')
    return User.objects.filter(is_active=True).exclude(role=provider_role).order_by('pk')


def _audit(actor, action, **details):
    ActivityLog.objects.create(actor=actor, action=action, details=details)


def _notify_daily_code(day, code, actor=None):
    """Send the secret only through Hub's in-app notification records.

    CLOSE_DAY_FINALIZED is intentionally reused as the existing daily-event route:
    it links to staff_close_day and is excluded from browser push delivery. The
    visible title/message identify this as a daily-code event; the secret never
    enters a lock-screen/browser-push payload.
    """
    from core.models import NotificationEvent, NotificationLog, NotificationRecipient

    users = list(_staff_users())
    event = NotificationEvent.objects.create(
        event_type=NotificationEvent.EventType.CLOSE_DAY_FINALIZED,
        title_ar=f'رمز الفريق ليوم {day.business_date} جاهز',
        message_ar=f'رمز اليوم: {code} — صالح حتى إغلاق يوم العمل أو تغييره من الإدارة.',
        created_by=actor if getattr(actor, 'is_authenticated', False) else None,
    )
    NotificationRecipient.objects.bulk_create([
        NotificationRecipient(
            notification_event=event,
            user=user,
            role=getattr(user, 'role', '') or '',
        )
        for user in users
    ])
    NotificationLog.objects.create(
        notification_event=event,
        channel=NotificationLog.Channel.SYSTEM,
        recipient_role='staff',
        status=NotificationLog.Status.SENT,
        sent_at=timezone.now(),
    )
    now = timezone.now()
    for user in users:
        receipt, _ = StaffDailyCodeReceipt.objects.get_or_create(
            business_day=day,
            user=user,
            defaults={'code_version': day.code_version},
        )
        receipt.code_version = day.code_version
        receipt.notified_at = now
        receipt.viewed_at = None
        receipt.first_used_at = None
        receipt.last_used_at = None
        receipt.use_count = 0
        receipt.save(update_fields=[
            'code_version', 'notified_at', 'viewed_at', 'first_used_at',
            'last_used_at', 'use_count', 'updated_at',
        ])
    return event


@transaction.atomic
def open_business_day(*, actor, business_date=None, cutoff_hour=None):
    business_date = business_date or current_business_date()
    cutoff_hour = int(cutoff_hour if cutoff_hour is not None else getattr(settings, 'BUSINESS_DAY_CUTOFF_HOUR', 4))
    if not 0 <= cutoff_hour <= 23:
        raise ValidationError('ساعة نهاية يوم العمل يجب أن تكون بين 0 و23.')

    existing = BusinessDay.objects.select_for_update().filter(business_date=business_date).first()
    if existing:
        if existing.status == BusinessDay.Status.CLOSED:
            raise ValidationError('هذا اليوم مغلق. يجب إعادة فتحه بصلاحية الإدارة بدلاً من إنشاء يوم جديد.')
        return existing
    other_open = BusinessDay.objects.select_for_update().filter(
        status__in=[BusinessDay.Status.OPEN, BusinessDay.Status.CLOSING, BusinessDay.Status.REOPENED]
    ).first()
    if other_open:
        raise ValidationError(f'يوجد يوم تشغيلي مفتوح بالفعل: {other_open.business_date}.')

    day = BusinessDay.objects.create(
        business_date=business_date,
        status=BusinessDay.Status.OPEN,
        cutoff_hour=cutoff_hour,
        opened_at=timezone.now(),
        opened_by=actor,
    )
    _audit(actor, 'business_day.opened', business_day_id=day.pk, business_date=str(day.business_date))
    issue_daily_code(day, actor=actor)
    return day


@transaction.atomic
def issue_daily_code(day, *, actor=None):
    day = BusinessDay.objects.select_for_update().get(pk=day.pk)
    if day.status == BusinessDay.Status.CLOSED:
        raise ValidationError('لا يمكن إصدار رمز ليوم مغلق.')
    code = f'{secrets.randbelow(10 ** STAFF_CODE_DIGITS):0{STAFF_CODE_DIGITS}d}'
    day.code_version += 1
    day.code_hash = make_password(code)
    day.code_ciphertext = _cipher().encrypt(code.encode('ascii')).decode('ascii')
    day.code_issued_at = timezone.now()
    day.save(update_fields=[
        'code_version', 'code_hash', 'code_ciphertext', 'code_issued_at', 'updated_at'
    ])
    _notify_daily_code(day, code, actor=actor)
    _audit(
        actor, 'business_day.staff_code_issued', business_day_id=day.pk,
        business_date=str(day.business_date), code_version=day.code_version,
    )
    return code


def reveal_daily_code(day, *, user):
    if day.status == BusinessDay.Status.CLOSED or not day.code_ciphertext:
        raise ValidationError('رمز هذا اليوم غير متاح.')
    try:
        code = _cipher().decrypt(day.code_ciphertext.encode('ascii')).decode('ascii')
    except (InvalidToken, ValueError) as exc:
        raise ValidationError('تعذر قراءة رمز اليوم. اطلب من الإدارة تغييره.') from exc
    receipt, _ = StaffDailyCodeReceipt.objects.get_or_create(
        business_day=day,
        user=user,
        defaults={'code_version': day.code_version},
    )
    receipt.code_version = day.code_version
    if receipt.viewed_at is None:
        receipt.viewed_at = timezone.now()
    receipt.save(update_fields=['code_version', 'viewed_at', 'updated_at'])
    _audit(user, 'business_day.staff_code_viewed', business_day_id=day.pk, code_version=day.code_version)
    return code


def verify_daily_code(day, code, *, user=None):
    if day.status == BusinessDay.Status.CLOSED or not day.code_hash:
        return False
    normalized = ''.join(ch for ch in str(code or '') if ch.isdigit())
    if len(normalized) != STAFF_CODE_DIGITS or not check_password(normalized, day.code_hash):
        _audit(user, 'business_day.staff_code_failed', business_day_id=day.pk, code_version=day.code_version)
        return False
    if user is not None:
        receipt, _ = StaffDailyCodeReceipt.objects.get_or_create(
            business_day=day, user=user, defaults={'code_version': day.code_version},
        )
        now = timezone.now()
        receipt.code_version = day.code_version
        if receipt.first_used_at is None:
            receipt.first_used_at = now
        receipt.last_used_at = now
        receipt.use_count += 1
        receipt.save(update_fields=[
            'code_version', 'first_used_at', 'last_used_at', 'use_count', 'updated_at'
        ])
    _audit(user, 'business_day.staff_code_verified', business_day_id=day.pk, code_version=day.code_version)
    return True


def _paid_amount(order):
    return sum(
        int(payment.amount_syp or 0)
        for payment in order.payments.all()
        if payment.is_active and not payment.is_reversed and payment.method != Payment.Method.UNPAID
    )


def _order_balance(order):
    return max(int(order.total_syp or 0) - _paid_amount(order), 0)


def build_reconciliation(day):
    start, end = business_day_bounds(day)
    exceptions = []

    orders = list(
        Order.objects.filter(created_at__gte=start, created_at__lt=end)
        .prefetch_related('payments')
        .order_by('created_at')
    )
    for order in orders:
        if order.status not in TERMINAL_ORDER_STATUSES:
            exceptions.append({
                'fingerprint': f'order:{order.pk}:status',
                'category': BusinessDayException.Category.ORDER,
                'severity': BusinessDayException.Severity.BLOCKER,
                'object_type': 'Order', 'object_id': str(order.pk),
                'title_ar': f'الطلب {order.display_number} ما زال مفتوحاً',
                'details': {'status': order.status},
            })
        balance = _order_balance(order)
        if order.status != Order.Status.CANCELLED and balance > 0:
            exceptions.append({
                'fingerprint': f'order:{order.pk}:unpaid',
                'category': BusinessDayException.Category.ORDER,
                'severity': BusinessDayException.Severity.BLOCKER,
                'object_type': 'Order', 'object_id': str(order.pk),
                'title_ar': f'الطلب {order.display_number} عليه رصيد غير مدفوع',
                'details': {'remaining_syp': balance},
            })

    active_sessions = list(
        InternetSession.objects.filter(status=InternetSession.Status.ACTIVE, started_at__lt=end)
        .select_related('entitlement', 'visit')
        .order_by('started_at')
    )
    for session in active_sessions:
        metered = session.billing_mode == InternetSession.BillingMode.OPEN_METERED
        exceptions.append({
            'fingerprint': f'internet:{session.pk}:active',
            'category': BusinessDayException.Category.INTERNET,
            'severity': BusinessDayException.Severity.BLOCKER if metered else BusinessDayException.Severity.WARNING,
            'object_type': 'InternetSession', 'object_id': str(session.pk),
            'title_ar': 'جلسة إنترنت مدفوعة ما زالت تعمل' if metered else 'جلسة إنترنت ما زالت فعالة',
            'details': {'billing_mode': session.billing_mode, 'started_at': session.started_at.isoformat() if session.started_at else None},
        })

    open_visits = list(
        HubVisit.objects.filter(status=HubVisit.Status.OPEN, opened_at__lt=end).order_by('opened_at')
    )
    for visit in open_visits:
        exceptions.append({
            'fingerprint': f'visit:{visit.pk}:open',
            'category': BusinessDayException.Category.VISIT,
            'severity': BusinessDayException.Severity.WARNING,
            'object_type': 'HubVisit', 'object_id': str(visit.pk),
            'title_ar': f'زيارة #{visit.pk} ما زالت مفتوحة',
            'details': {'opened_at': visit.opened_at.isoformat() if visit.opened_at else None},
        })

    shifts = list(
        Shift.objects.filter(opened_at__lt=end).filter(closed_at__isnull=True).select_related('cashbox', 'opened_by')
    )
    for shift in shifts:
        exceptions.append({
            'fingerprint': f'shift:{shift.pk}:open',
            'category': BusinessDayException.Category.SHIFT,
            'severity': BusinessDayException.Severity.BLOCKER,
            'object_type': 'Shift', 'object_id': str(shift.pk),
            'title_ar': f'مناوبة {shift.opened_by} لم تُغلق',
            'details': {'cashbox_id': shift.cashbox_id, 'opened_at': shift.opened_at.isoformat()},
        })

    day_shifts = Shift.objects.filter(opened_at__gte=start, opened_at__lt=end).exclude(cashbox__isnull=True)
    cashbox_ids = set(day_shifts.values_list('cashbox_id', flat=True))
    for cashbox_id in sorted(cashbox_ids):
        if not DailyClose.objects.filter(
            account_id=cashbox_id,
            business_date=day.business_date,
            status=DailyClose.Status.CLOSED,
            is_finalized=True,
        ).exists():
            account = FinancialAccount.objects.filter(pk=cashbox_id).first()
            exceptions.append({
                'fingerprint': f'finance:{cashbox_id}:daily-close',
                'category': BusinessDayException.Category.FINANCE,
                'severity': BusinessDayException.Severity.BLOCKER,
                'object_type': 'FinancialAccount', 'object_id': str(cashbox_id),
                'title_ar': f'الحساب {account or cashbox_id} يحتاج إغلاقاً مالياً نهائياً',
                'details': {'account_id': cashbox_id},
            })

    for close in DailyClose.objects.filter(business_date=day.business_date, is_finalized=False):
        exceptions.append({
            'fingerprint': f'finance:close:{close.pk}:not-final',
            'category': BusinessDayException.Category.FINANCE,
            'severity': BusinessDayException.Severity.BLOCKER,
            'object_type': 'DailyClose', 'object_id': str(close.pk),
            'title_ar': f'إغلاق مالي #{close.pk} غير نهائي',
            'details': {'status': close.status, 'account_id': close.account_id},
        })

    return {
        'start': start,
        'end': end,
        'orders_count': len(orders),
        'active_sessions_count': len(active_sessions),
        'open_visits_count': len(open_visits),
        'open_shifts_count': len(shifts),
        'exceptions': exceptions,
        'blocker_count': sum(1 for item in exceptions if item['severity'] == BusinessDayException.Severity.BLOCKER),
        'warning_count': sum(1 for item in exceptions if item['severity'] == BusinessDayException.Severity.WARNING),
    }


@transaction.atomic
def sync_exceptions(day, *, actor=None):
    day = BusinessDay.objects.select_for_update().get(pk=day.pk)
    report = build_reconciliation(day)
    active_fingerprints = set()
    for item in report['exceptions']:
        active_fingerprints.add(item['fingerprint'])
        obj, created = BusinessDayException.objects.get_or_create(
            business_day=day,
            fingerprint=item['fingerprint'],
            defaults={
                'category': item['category'], 'severity': item['severity'],
                'object_type': item['object_type'], 'object_id': item['object_id'],
                'title_ar': item['title_ar'], 'details': item['details'],
            },
        )
        if not created and obj.status != BusinessDayException.Status.CARRIED_FORWARD:
            obj.category = item['category']
            obj.severity = item['severity']
            obj.object_type = item['object_type']
            obj.object_id = item['object_id']
            obj.title_ar = item['title_ar']
            obj.details = item['details']
            obj.status = BusinessDayException.Status.OPEN
            obj.resolution_note = ''
            obj.resolved_by = None
            obj.resolved_at = None
            obj.save()

    stale = BusinessDayException.objects.filter(
        business_day=day,
        status=BusinessDayException.Status.OPEN,
    ).exclude(fingerprint__in=active_fingerprints)
    stale.update(
        status=BusinessDayException.Status.RESOLVED,
        resolution_note='حُل تلقائياً بعد تحديث حالة النظام.',
        resolved_by=actor,
        resolved_at=timezone.now(),
    )
    return report


@transaction.atomic
def start_closing(day, *, actor):
    day = BusinessDay.objects.select_for_update().get(pk=day.pk)
    if day.status == BusinessDay.Status.CLOSED:
        raise ValidationError('اليوم مغلق بالفعل.')
    day.status = BusinessDay.Status.CLOSING
    day.closing_started_at = day.closing_started_at or timezone.now()
    day.save(update_fields=['status', 'closing_started_at', 'updated_at'])
    report = sync_exceptions(day, actor=actor)
    _audit(actor, 'business_day.closing_started', business_day_id=day.pk, blocker_count=report['blocker_count'])
    return day, report


def close_open_internet_sessions(day, *, actor):
    _, end = business_day_bounds(day)
    sessions = list(
        InternetSession.objects.filter(status=InternetSession.Status.ACTIVE, started_at__lt=end)
        .select_related('entitlement')
        .order_by('started_at')
    )
    closed = 0
    failures = []
    for session in sessions:
        try:
            if session.billing_mode == InternetSession.BillingMode.OPEN_METERED and not session.entitlement_id:
                ended = finalize_visit_metered_session(session, actor=actor)
            else:
                ended = end_usage_session(session, actor=actor)
                if ended.entitlement_id:
                    enqueue_network_operation(
                        ended.entitlement,
                        InternetNetworkOperation.Operation.DEAUTHENTICATE,
                        reason='business day close',
                        idempotency_key=f'entitlement:{ended.entitlement.public_code}:business-day:{day.business_date}:deauthenticate',
                    )
                elif ended.network_provider == InternetSession.NetworkProvider.MIKROTIK:
                    enqueue_session_network_operation(
                        ended,
                        InternetSessionNetworkOperation.Operation.DISCONNECT,
                        reason='business day close',
                        idempotency_key=f'session:{ended.public_code}:business-day:{day.business_date}:disconnect',
                        process_after_commit=False,
                    )
            closed += 1
        except Exception as exc:  # keep one broken session from hiding the rest
            failures.append({'session_id': session.pk, 'error': exc.__class__.__name__})
    _audit(
        actor, 'business_day.internet_sessions_closed', business_day_id=day.pk,
        closed_count=closed, failures=failures,
    )
    sync_exceptions(day, actor=actor)
    return {'closed': closed, 'failures': failures}


def close_safe_visits(day, *, actor):
    _, end = business_day_bounds(day)
    visits = HubVisit.objects.filter(status=HubVisit.Status.OPEN, opened_at__lt=end).order_by('opened_at')
    closed = 0
    skipped = 0
    now = timezone.now()
    for visit in visits:
        if InternetSession.objects.filter(visit=visit, status=InternetSession.Status.ACTIVE).exists():
            skipped += 1
            continue
        orders = list(Order.objects.filter(visit=visit).prefetch_related('payments'))
        unsafe = any(order.status not in TERMINAL_ORDER_STATUSES or _order_balance(order) > 0 for order in orders)
        if unsafe:
            skipped += 1
            continue
        HubVisit.objects.filter(pk=visit.pk, status=HubVisit.Status.OPEN).update(
            status=HubVisit.Status.CLOSED,
            closed_at=now,
            last_activity_at=now,
            updated_at=now,
        )
        closed += 1
        _audit(actor, 'business_day.visit_auto_closed', business_day_id=day.pk, visit_id=visit.pk)
    sync_exceptions(day, actor=actor)
    return {'closed': closed, 'skipped': skipped}


@transaction.atomic
def carry_forward_exception(exception, *, actor, note):
    exception = BusinessDayException.objects.select_for_update().get(pk=exception.pk)
    if exception.severity == BusinessDayException.Severity.BLOCKER:
        raise ValidationError('العناصر المانعة للإغلاق يجب حلها ولا يمكن ترحيلها مباشرة.')
    note = (note or '').strip()
    if not note:
        raise ValidationError('سبب الترحيل مطلوب.')
    exception.status = BusinessDayException.Status.CARRIED_FORWARD
    exception.resolution_note = note
    exception.resolved_by = actor
    exception.resolved_at = timezone.now()
    exception.save(update_fields=['status', 'resolution_note', 'resolved_by', 'resolved_at', 'updated_at'])
    _audit(actor, 'business_day.exception_carried_forward', business_day_id=exception.business_day_id, exception_id=exception.pk)
    return exception


@transaction.atomic
def finalize_business_day(day, *, actor):
    day = BusinessDay.objects.select_for_update().get(pk=day.pk)
    if day.status == BusinessDay.Status.CLOSED:
        return day
    report = sync_exceptions(day, actor=actor)
    blockers = BusinessDayException.objects.filter(
        business_day=day,
        status=BusinessDayException.Status.OPEN,
        severity=BusinessDayException.Severity.BLOCKER,
    ).count()
    if blockers:
        raise ValidationError(f'لا يمكن إغلاق اليوم قبل حل {blockers} عنصر/عناصر مانعة.')

    snapshot = {
        'business_date': day.business_date.isoformat(),
        'cutoff_hour': day.cutoff_hour,
        'closed_at': timezone.now().isoformat(),
        'orders_count': report['orders_count'],
        'active_sessions_at_check': report['active_sessions_count'],
        'open_visits_at_check': report['open_visits_count'],
        'open_shifts_at_check': report['open_shifts_count'],
        'warnings_open': BusinessDayException.objects.filter(
            business_day=day,
            status=BusinessDayException.Status.OPEN,
            severity=BusinessDayException.Severity.WARNING,
        ).count(),
        'carried_forward': BusinessDayException.objects.filter(
            business_day=day,
            status=BusinessDayException.Status.CARRIED_FORWARD,
        ).count(),
    }
    day.status = BusinessDay.Status.CLOSED
    day.closed_at = timezone.now()
    day.closed_by = actor
    day.closing_snapshot = snapshot
    day.code_ciphertext = ''
    day.save(update_fields=[
        'status', 'closed_at', 'closed_by', 'closing_snapshot', 'code_ciphertext', 'updated_at'
    ])
    _audit(actor, 'business_day.closed', business_day_id=day.pk, snapshot=snapshot)

    from core.notifications import create_notification
    create_notification(
        'close_day_finalized',
        f'تم إغلاق يوم {day.business_date}',
        f'تم الإغلاق التشغيلي. عناصر مرحّلة: {snapshot["carried_forward"]}.',
        created_by=actor,
    )
    return day


@transaction.atomic
def reopen_business_day(day, *, actor, reason):
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('سبب إعادة فتح اليوم مطلوب.')
    day = BusinessDay.objects.select_for_update().get(pk=day.pk)
    if day.status != BusinessDay.Status.CLOSED:
        raise ValidationError('يمكن إعادة فتح يوم مغلق فقط.')
    day.status = BusinessDay.Status.REOPENED
    day.reopened_at = timezone.now()
    day.reopened_by = actor
    day.reopen_reason = reason
    day.closed_at = None
    day.closed_by = None
    day.save(update_fields=[
        'status', 'reopened_at', 'reopened_by', 'reopen_reason',
        'closed_at', 'closed_by', 'updated_at',
    ])
    _audit(actor, 'business_day.reopened', business_day_id=day.pk, reason=reason)
    issue_daily_code(day, actor=actor)
    return day
