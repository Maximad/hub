"""Safe read-only browser tracking; no contact data or ownership credentials escape."""
from dataclasses import dataclass
from datetime import datetime

from django.urls import reverse
from django.utils import timezone

from core.services.public_homepage import DAMASCUS
from reservations.attendance import digest
from reservations.models import EventAttendanceRequest as Request

OWNER_SESSION_KEY = 'public_attendance_owner'


def owner_digest(request):
    token = request.session.get(OWNER_SESSION_KEY)
    return digest(token) if isinstance(token, str) and token else None


def owned_attendance_requests(owner_hash):
    return Request.objects.filter(owner_hash=owner_hash).select_related('event', 'reservation').order_by('-created_at', '-pk')


@dataclass(frozen=True)
class PublicAttendanceItem:
    title: str
    party_size: int
    status_label: str
    url: str
    can_withdraw: bool
    approved: bool
    requested_at: datetime
    event_starts_at: datetime | None = None
    event_url: str = ''


def public_attendance_item(row):
    label = dict(Request.Status.choices).get(row.status, 'راجع الفريق للتحقق من حالة الطلب')
    if row.status == Request.Status.APPROVED:
        if row.reservation_id:
            label = {'confirmed': 'الفريق أكّد حجز الحضور', 'cancelled': 'ألغى الفريق الحجز',
                     'completed': 'انتهى الحضور', 'no_show': 'سُجّل عدم حضور',
                     'pending': 'الحجز بانتظار مراجعة الفريق'}.get(row.reservation.status, 'راجع الفريق')
        else:
            label = 'راجع الفريق للتحقق من حالة الحجز'
    # Only an actively published public source can supply a current date/link.
    # Existing private receipts retain the original public title snapshot, but
    # never reveal a later private title, description, room or schedule.
    public_source = row.event and row.event.status == 'published' and row.event.is_public
    return PublicAttendanceItem(
        title=row.event_title, party_size=row.party_size, status_label=label,
        url=reverse('public_attendance_receipt', kwargs={'uuid': row.uuid}),
        can_withdraw=row.status == Request.Status.PENDING, approved=row.status == Request.Status.APPROVED,
        requested_at=timezone.localtime(row.created_at, DAMASCUS),
        event_starts_at=timezone.localtime(row.event.starts_at, DAMASCUS) if public_source else None,
        event_url=reverse('public_event_detail', kwargs={'uuid': row.event.uuid}) if public_source else '',
    )
