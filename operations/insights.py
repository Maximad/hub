from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Sum

from core.models import DailyClose, InternetSession, NotificationEvent, NotificationLog, NotificationRecipient, Order, Payment
from django.utils import timezone

from .models import BusinessDayChecklistItem, HandoverNote, StaffDailyCodeReceipt
from .services import business_day_bounds


def _paid_amount(order):
    return sum(
        int(payment.amount_syp or 0)
        for payment in order.payments.all()
        if payment.is_active and not payment.is_reversed and payment.method != Payment.Method.UNPAID
    )


def business_day_metrics(day):
    start, end = business_day_bounds(day)
    orders = list(
        Order.objects.filter(created_at__gte=start, created_at__lt=end)
        .prefetch_related('payments')
    )
    completed_orders = [order for order in orders if order.status != Order.Status.CANCELLED]
    gross_sales = sum(int(order.total_syp or 0) for order in completed_orders)
    paid_total = sum(_paid_amount(order) for order in completed_orders)
    discounts = sum(int(order.discount_syp or 0) for order in completed_orders)
    cancelled = sum(1 for order in orders if order.status == Order.Status.CANCELLED)
    internet_revenue = InternetSession.objects.filter(
        started_at__gte=start,
        started_at__lt=end,
    ).aggregate(total=Sum('payable_total_syp'))['total'] or 0
    closes = DailyClose.objects.filter(
        business_date=day.business_date,
        status=DailyClose.Status.CLOSED,
        is_finalized=True,
    )
    cash_difference = sum(int(close.cash_difference_syp or 0) for close in closes)
    handover_open = HandoverNote.objects.exclude(status=HandoverNote.Status.RESOLVED).filter(
        business_day__business_date__lte=day.business_date,
    ).count()
    return {
        'orders_count': len(orders),
        'gross_sales_syp': gross_sales,
        'paid_total_syp': paid_total,
        'discounts_syp': discounts,
        'cancelled_orders_count': cancelled,
        'internet_revenue_syp': int(internet_revenue or 0),
        'cash_difference_syp': cash_difference,
        'handover_open_count': handover_open,
    }


def detect_anomalies(day):
    metrics = business_day_metrics(day)
    anomalies = []
    cash_threshold = int(getattr(settings, 'BUSINESS_DAY_CASH_DIFFERENCE_WARNING_SYP', 5000))
    discount_threshold = int(getattr(settings, 'BUSINESS_DAY_DISCOUNT_WARNING_SYP', 10000))
    cancellation_threshold = int(getattr(settings, 'BUSINESS_DAY_CANCELLATION_WARNING_COUNT', 3))

    if abs(metrics['cash_difference_syp']) >= cash_threshold > 0:
        anomalies.append({
            'code': 'cash_difference',
            'severity': 'warning',
            'title_ar': 'فرق نقدي يحتاج مراجعة',
            'detail_ar': f'مجموع فرق الصناديق لليوم: {metrics["cash_difference_syp"]} ل.س.',
        })
    if metrics['discounts_syp'] >= discount_threshold > 0:
        anomalies.append({
            'code': 'high_discounts',
            'severity': 'warning',
            'title_ar': 'قيمة خصومات مرتفعة',
            'detail_ar': f'إجمالي الخصومات: {metrics["discounts_syp"]} ل.س.',
        })
    if metrics['cancelled_orders_count'] >= cancellation_threshold > 0:
        anomalies.append({
            'code': 'many_cancellations',
            'severity': 'warning',
            'title_ar': 'عدد إلغاءات أعلى من المعتاد',
            'detail_ar': f'تم إلغاء {metrics["cancelled_orders_count"]} طلبات ضمن يوم العمل.',
        })

    unacknowledged = 0
    assignment_user_ids = list(day.staff_assignments.values_list('user_id', flat=True))
    if assignment_user_ids:
        acknowledged_ids = set(
            StaffDailyCodeReceipt.objects.filter(
                business_day=day,
                user_id__in=assignment_user_ids,
                code_version=day.code_version,
            ).exclude(viewed_at__isnull=True, first_used_at__isnull=True).values_list('user_id', flat=True)
        )
        unacknowledged = len(set(assignment_user_ids) - acknowledged_ids)
    if unacknowledged:
        anomalies.append({
            'code': 'staff_code_unacknowledged',
            'severity': 'info',
            'title_ar': 'أعضاء من فريق اليوم لم يؤكدوا الرمز',
            'detail_ar': f'{unacknowledged} من الموظفين المعيّنين لم يسجّلوا الاطلاع/الاستخدام بعد.',
        })

    high_handover = HandoverNote.objects.filter(
        priority=HandoverNote.Priority.HIGH,
    ).exclude(status=HandoverNote.Status.RESOLVED).filter(
        business_day__business_date__lte=day.business_date,
    ).count()
    if high_handover:
        anomalies.append({
            'code': 'high_priority_handover',
            'severity': 'warning',
            'title_ar': 'ملاحظات تسليم مهمة ما زالت مفتوحة',
            'detail_ar': f'{high_handover} ملاحظة مهمة تحتاج متابعة.',
        })

    pending_opening = BusinessDayChecklistItem.objects.filter(
        business_day=day,
        stage=BusinessDayChecklistItem.Stage.OPENING,
        is_required=True,
        status=BusinessDayChecklistItem.Status.PENDING,
    ).count()
    if pending_opening:
        anomalies.append({
            'code': 'opening_incomplete',
            'severity': 'warning',
            'title_ar': 'افتتاح اليوم لم يكتمل',
            'detail_ar': f'{pending_opening} بنود افتتاح مطلوبة بقيت بلا تأكيد.',
        })
    return anomalies


def send_owner_digest(day, *, actor=None):
    metrics = business_day_metrics(day)
    anomalies = detect_anomalies(day)
    User = get_user_model()
    owners = list(User.objects.filter(is_active=True, is_superuser=True))
    if not owners:
        owners = list(User.objects.filter(is_active=True, role='admin'))
    if not owners:
        return None

    anomaly_text = 'لا توجد مؤشرات غير اعتيادية.' if not anomalies else ' | '.join(item['title_ar'] for item in anomalies[:4])
    message = (
        f'المبيعات {metrics["gross_sales_syp"]} ل.س. — '
        f'{metrics["orders_count"]} طلب — '
        f'إنترنت {metrics["internet_revenue_syp"]} ل.س. — '
        f'فرق النقد {metrics["cash_difference_syp"]} ل.س. — '
        f'الإلغاءات {metrics["cancelled_orders_count"]}. '
        f'{anomaly_text}'
    )
    event = NotificationEvent.objects.create(
        event_type=NotificationEvent.EventType.CLOSE_DAY_FINALIZED,
        title_ar=f'ملخص يوم هَبّ {day.business_date}',
        message_ar=message,
        created_by=actor if getattr(actor, 'is_authenticated', False) else None,
    )
    NotificationRecipient.objects.bulk_create([
        NotificationRecipient(notification_event=event, user=user, role='admin') for user in owners
    ])
    NotificationLog.objects.create(
        notification_event=event,
        channel=NotificationLog.Channel.SYSTEM,
        recipient_role='owner',
        status=NotificationLog.Status.SENT,
        sent_at=timezone.now(),
    )
    return event
