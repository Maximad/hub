import uuid
from django.db import models
from django.core.exceptions import ValidationError


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
