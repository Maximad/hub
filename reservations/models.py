import uuid
from django.db import models


class Reservation(models.Model):
    class ReservationType(models.TextChoices):
        TABLE = 'table', 'Table'
        ROOM = 'room', 'Room'
        EVENT_TABLE = 'event_table', 'Event Table'
        WORKSPACE = 'workspace', 'Workspace'
        PRIVATE_EVENT = 'private_event', 'Private Event'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        CONFIRMED = 'confirmed', 'Confirmed'
        CANCELLED = 'cancelled', 'Cancelled'
        COMPLETED = 'completed', 'Completed'
        NO_SHOW = 'no_show', 'No Show'

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    reservation_type = models.CharField(max_length=30, choices=ReservationType.choices, default=ReservationType.TABLE)
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=30)
    table_area = models.ForeignKey('core.TableArea', on_delete=models.SET_NULL, null=True, blank=True)
    event = models.ForeignKey('events.Event', on_delete=models.SET_NULL, null=True, blank=True)
    reservation_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField(null=True, blank=True)
    party_size = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    deposit_syp = models.IntegerField(default=0)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.name} — {self.reservation_date} {self.start_time} — {self.phone}'
