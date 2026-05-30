import uuid
from django.db import models


class Event(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PUBLISHED = 'published', 'Published'
        CANCELLED = 'cancelled', 'Cancelled'
        COMPLETED = 'completed', 'Completed'

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    title_ar = models.CharField(max_length=200)
    title_en = models.CharField(max_length=200, blank=True)
    description_ar = models.TextField(blank=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    location_area = models.ForeignKey('core.TableArea', on_delete=models.SET_NULL, null=True, blank=True)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    is_public = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title_ar or self.title_en or str(self.uuid)[:8]


class EventTicketType(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='ticket_types')
    product = models.ForeignKey('core.Product', on_delete=models.SET_NULL, null=True, blank=True)
    name_ar = models.CharField(max_length=120)
    price_syp = models.PositiveIntegerField()
    capacity = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        event_title = self.event.title_ar if self.event_id else ''
        return f'{self.name_ar} — {event_title}' if event_title else self.name_ar
