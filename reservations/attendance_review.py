"""Read-only guidance; POST approval remains the locked domain authority."""
from dataclasses import dataclass

from django.utils import timezone

from reservations.attendance import accepting_events
from reservations.models import EventAttendanceRequest
from reservations.services import event_reserved_party_size


@dataclass(frozen=True)
class AttendanceReviewInfo:
    can_approve: bool
    reason: str
    capacity: int | None = None
    reserved: int | None = None
    available: int | None = None


def attendance_review_info(row, *, now=None):
    if row.status == EventAttendanceRequest.Status.APPROVED and not row.reservation_id:
        return AttendanceReviewInfo(False, 'الطلب معتمد لكن الحجز المرتبط غير موجود؛ راجع السجل قبل أي إجراء.')
    if row.status != EventAttendanceRequest.Status.PENDING:
        return AttendanceReviewInfo(False, 'تمت معالجة هذا الطلب. إدارة الحجز المعتمد من صفحة الحجز.')
    if row.event_id is None:
        return AttendanceReviewInfo(False, 'حُذفت الفعالية المرتبطة؛ يمكن رفض الطلب دون إنشاء حجز.')
    now = now or timezone.now()
    event = accepting_events(now).filter(pk=row.event_id).first()
    if event is None:
        source = row.event
        if source.status == 'cancelled':
            reason = 'الفعالية ملغاة؛ لا يمكن اعتماد الحضور.'
        elif source.starts_at <= now:
            reason = 'بدأ موعد الفعالية أو مضى؛ أُغلق اعتماد الطلبات.'
        elif source.status != 'published' or not source.is_public:
            reason = 'الفعالية غير منشورة للزوار؛ لا يمكن اعتماد الطلب.'
        elif not source.accepts_attendance_requests:
            reason = 'أُغلق استقبال طلبات الحضور لهذه الفعالية.'
        else:
            reason = 'السعة غير محددة أو غير صالحة؛ راجع إعداد الفعالية.'
        return AttendanceReviewInfo(False, reason)
    reserved = event_reserved_party_size(event.pk)
    available = max(event.capacity-reserved, 0)
    enough = row.party_size <= available
    return AttendanceReviewInfo(
        enough, 'السعة الحالية تكفي للعدد المطلوب.' if enough else 'السعة الحالية لا تكفي للعدد المطلوب؛ يمكن رفض الطلب أو مراجعته لاحقاً.',
        capacity=event.capacity, reserved=reserved, available=available,
    )
