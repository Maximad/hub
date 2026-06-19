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



class EventMedia(models.Model):
    class Role(models.TextChoices):
        POSTER = 'poster', 'ملصق الفعالية'
        COVER = 'cover', 'صورة الغلاف'
        GALLERY = 'gallery', 'معرض الصور'

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='media')
    media_asset = models.ForeignKey('catalog.MediaAsset', on_delete=models.CASCADE, related_name='event_media', verbose_name='صورة الفعالية')
    role = models.CharField('الدور', max_length=20, choices=Role.choices, default=Role.GALLERY)
    is_primary = models.BooleanField('صورة أساسية', default=False)
    is_active = models.BooleanField('نشط', default=True)
    sort_order = models.IntegerField('ترتيب العرض', default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['role', 'sort_order', '-is_primary', 'created_at']
        indexes = [models.Index(fields=['event', 'role', 'is_active', 'is_primary', 'sort_order'])]
        verbose_name = 'صورة الفعالية'
        verbose_name_plural = 'صور الفعاليات'

    def __str__(self):
        return f'{self.event} — {self.get_role_display()}'
