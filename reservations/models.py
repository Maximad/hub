import uuid
from django.db import models
from django.db import transaction
from django.core.exceptions import ValidationError
from django.apps import apps


class Reservation(models.Model):
    class ReservationType(models.TextChoices):
        REGULAR = 'regular', 'حجز عادي'
        EVENT = 'event', 'حجز فعالية'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        CONFIRMED = 'confirmed', 'Confirmed'
        CANCELLED = 'cancelled', 'Cancelled'
        COMPLETED = 'completed', 'Completed'
        NO_SHOW = 'no_show', 'No Show'

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    reservation_type = models.CharField(max_length=30, choices=ReservationType.choices, default=ReservationType.REGULAR)
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=30)
    table_area = models.ForeignKey('core.TableArea', on_delete=models.SET_NULL, null=True, blank=True)
    room = models.ForeignKey('core.Room', on_delete=models.SET_NULL, null=True, blank=True, related_name='reservations')
    event = models.ForeignKey('events.Event', on_delete=models.SET_NULL, null=True, blank=True)
    visit = models.OneToOneField(
        'core.HubVisit',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reservation',
    )
    reservation_date = models.DateField(null=True, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    party_size = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    deposit_syp = models.IntegerField(default=0)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(
                fields=['reservation_date', 'start_time', 'end_time', 'status'],
                name='reservation_interval_idx',
            ),
            models.Index(
                fields=['event', 'status'],
                name='reservation_event_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(end_time__isnull=True)
                    | models.Q(start_time__isnull=True)
                    | models.Q(end_time__gt=models.F('start_time'))
                ),
                name='reservation_end_after_start',
            ),
        ]

    TRANSITIONS = {
        Status.PENDING: frozenset({Status.CONFIRMED, Status.CANCELLED}),
        Status.CONFIRMED: frozenset({Status.COMPLETED, Status.NO_SHOW, Status.CANCELLED}),
        Status.CANCELLED: frozenset(),
        Status.COMPLETED: frozenset(),
        Status.NO_SHOW: frozenset(),
    }
    CORRECTIONS = {
        Status.CANCELLED: frozenset({Status.PENDING, Status.CONFIRMED}),
        Status.COMPLETED: frozenset({Status.PENDING, Status.CONFIRMED}),
        Status.NO_SHOW: frozenset({Status.PENDING, Status.CONFIRMED}),
    }

    @classmethod
    def transition_status(cls, reservation_id, *, actor, new_status):
        return cls._change_status(reservation_id, actor=actor, new_status=new_status, reason='', correction=False)

    @classmethod
    def correct_status(cls, reservation_id, *, actor, new_status, reason):
        return cls._change_status(reservation_id, actor=actor, new_status=new_status, reason=reason, correction=True)

    @classmethod
    def _change_status(cls, reservation_id, *, actor, new_status, reason, correction):
        """Validate and persist a status change while holding the reservation row lock."""
        with transaction.atomic():
            reservation = (
                cls.objects.select_for_update(of=('self',))
                .select_related('visit')
                .get(pk=reservation_id)
            )
            old_status = reservation.status
            if reservation.visit_id and reservation.visit.status == 'open':
                raise ValidationError({'status': 'لا يمكن تغيير حالة الحجز أثناء جلسة مفتوحة. أغلق الجلسة المرتبطة أولاً.'})
            allowed = cls.CORRECTIONS.get(old_status, ()) if correction else cls.TRANSITIONS.get(old_status, ())
            if new_status not in allowed:
                raise ValidationError({'status': f'Transition from {old_status} to {new_status} is not permitted.'})
            if correction:
                if not (getattr(actor, 'is_active', False) and (getattr(actor, 'is_superuser', False) or getattr(actor, 'role', '') == 'admin')):
                    raise ValidationError({'status': 'Only an administrator may correct a terminal reservation.'})
                if not (reason or '').strip():
                    raise ValidationError({'reason': 'A reason is required for a reservation correction.'})
            reservation.status = new_status
            # Status changes can make a reservation active again.  Re-run the
            # domain availability/capacity checks under the same transaction
            # and row/resource locks before persisting it.
            from .services import _validate_for_status
            _validate_for_status(reservation)
            reservation.save(update_fields=['status', 'end_time', 'room', 'updated_at'])
            AuditEvent = apps.get_model('core', 'AuditEvent')
            AuditEvent.objects.create(
                actor=actor, action='reservation_status_correction' if correction else 'reservation_status_transition',
                source=reservation, before_snapshot={'status': old_status},
                after_snapshot={'status': new_status, 'reason': (reason or '').strip()}, channel='staff',
            )
            return reservation

    @property
    def allowed_status_transitions(self):
        return self.TRANSITIONS.get(self.status, frozenset())

    @property
    def operational_status_label(self):
        if self.visit_id:
            if self.visit.status == 'open':
                return 'داخل الجلسة'
            return 'انتهت الجلسة'
        labels = {
            self.Status.PENDING: 'بانتظار التأكيد',
            self.Status.CONFIRMED: 'مؤكد — بانتظار الوصول',
            self.Status.CANCELLED: 'ملغى',
            self.Status.COMPLETED: 'منتهٍ',
            self.Status.NO_SHOW: 'لم يحضر',
        }
        return labels.get(self.status, self.get_status_display())

    def __str__(self):
        return f'{self.name} — {self.effective_date or "—"} {self.effective_starts_at or "—"} — {self.phone}'

    @property
    def effective_date(self):
        return self.event.starts_at.date() if self.reservation_type == self.ReservationType.EVENT and self.event_id else self.reservation_date

    @property
    def effective_starts_at(self):
        return self.event.starts_at.time() if self.reservation_type == self.ReservationType.EVENT and self.event_id else self.start_time

    @property
    def effective_ends_at(self):
        if self.reservation_type == self.ReservationType.EVENT and self.event_id:
            return self.event.ends_at.time() if self.event.ends_at else None
        return self.end_time

    @property
    def effective_room(self):
        if self.reservation_type == self.ReservationType.EVENT and self.event_id:
            return self.event.room
        return self.room or (self.table_area.room if self.table_area_id else None)

    def clean(self):
        errors = {}
        if self.reservation_type == self.ReservationType.EVENT:
            if not self.event_id:
                errors['event'] = 'يرجى اختيار الفعالية.'
            self.reservation_date = self.start_time = self.end_time = None
            self.room = self.table_area = None
        else:
            if self.event_id:
                errors['event'] = 'لا يمكن ربط حجز عادي بفعالية.'
            if not self.reservation_date:
                errors['reservation_date'] = 'يرجى تحديد تاريخ الحجز.'
            if not self.start_time:
                errors['start_time'] = 'يرجى تحديد وقت بدء الحجز.'
            if self.start_time and self.end_time and self.end_time <= self.start_time:
                errors['end_time'] = 'وقت النهاية يجب أن يكون بعد وقت البداية.'
            if self.table_area_id and self.room_id and self.table_area.room_id != self.room_id:
                errors['table_area'] = 'الطاولة المختارة لا تتبع للمساحة المحددة.'
        if errors:
            raise ValidationError(errors)
