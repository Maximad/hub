import uuid
from django.db import models
from django.core.exceptions import ValidationError


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
    room = models.ForeignKey('core.Room', on_delete=models.SET_NULL, null=True, blank=True, related_name='events')
    capacity = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    is_public = models.BooleanField(default=True)
    accepts_attendance_requests = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(
            condition=models.Q(accepts_attendance_requests=False) | models.Q(capacity__isnull=False, capacity__gt=0),
            name='event_requests_require_capacity')]

    def clean(self):
        super().clean()
        if self.accepts_attendance_requests and not self.capacity:
            raise ValidationError({'capacity': 'حدّد سعة موجبة قبل فتح طلبات الحضور.'})

    def __str__(self):
        return self.title_ar or self.title_en or str(self.uuid)[:8]



class EventPerformance(models.Model):
    """One bookable occasion of a shared event; never a separate event."""
    class Status(models.TextChoices):
        SCHEDULED = 'scheduled', 'مجدول'
        CANCELLED = 'cancelled', 'ملغى'
        COMPLETED = 'completed', 'منتهٍ'

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='performances')
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    room = models.ForeignKey('core.Room', on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='event_performances')
    capacity = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SCHEDULED)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('starts_at', 'pk')
        constraints = [
            models.UniqueConstraint(fields=('event', 'starts_at'), name='unique_event_performance_time'),
            models.CheckConstraint(condition=models.Q(capacity__gt=0) | models.Q(capacity__isnull=True),
                                   name='event_performance_positive_capacity'),
        ]

    @property
    def effective_capacity(self):
        return self.capacity if self.capacity is not None else self.event.capacity

    @property
    def effective_room(self):
        return self.room or self.event.room

    def clean(self):
        super().clean()
        if self.ends_at and self.starts_at and self.ends_at <= self.starts_at:
            raise ValidationError({'ends_at': 'يجب أن يكون وقت النهاية بعد بداية العرض.'})
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous and any(getattr(previous, field) != getattr(self, field)
                    for field in ('starts_at', 'ends_at', 'room_id', 'capacity', 'status')):
                if self.reservations.exists() or self.attendance_requests.exists():
                    raise ValidationError('لا يمكن تعديل موعد له حجوزات أو طلبات حضور دون إجراء نقل/إلغاء مراجع.')

    def __str__(self):
        return f'{self.event} — {self.starts_at:%Y-%m-%d %H:%M}'


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


class EventExpenseAllocation(models.Model):
    """Event-specific assignment of an existing Hub expense, not a new expense.

    Financial posting, approval and payment remain in core. These allocations
    are partial evidence of direct event costs, never a full profitability claim.
    """
    event = models.ForeignKey(
        Event, on_delete=models.PROTECT, related_name='expense_allocations',
    )
    expense = models.ForeignKey(
        'core.Expense', on_delete=models.PROTECT, related_name='event_allocations',
    )
    amount_syp = models.PositiveIntegerField()
    rationale = models.CharField(max_length=200)
    allocated_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True,
        related_name='event_expense_allocations_made',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('event', 'expense'), name='uniq_event_expense_assignment'),
            models.CheckConstraint(condition=models.Q(amount_syp__gt=0), name='event_expense_amount_positive'),
        ]

    def clean(self):
        super().clean()
        if not (self.rationale or '').strip():
            raise ValidationError({'rationale': 'توضيح ارتباط المصروف بالفعالية مطلوب.'})


class VenueLayoutTemplate(models.Model):
    """Reusable *draft* geometry; never a legal occupancy or booking inventory."""
    class Kind(models.TextChoices):
        COMEDY = 'comedy', 'كوميديا وطاولات'
        THEATRE = 'theatre', 'مسرح وسينما'
        CABARET = 'cabaret', 'موسيقى وكباريه'
        WORKSHOP = 'workshop', 'ورشات وحوارات'
        STANDING = 'standing', 'حفلة ووقوف'
        CUSTOM = 'custom', 'تصميم مخصص'

    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.COMEDY)
    description = models.CharField(max_length=300, blank=True)
    layout = models.JSONField(default=dict)
    version = models.PositiveIntegerField(default=1)
    archived = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        'accounts.User', null=True, on_delete=models.SET_NULL,
        related_name='venue_templates_created',
    )
    updated_by = models.ForeignKey(
        'accounts.User', null=True, on_delete=models.SET_NULL,
        related_name='venue_templates_updated',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['kind', 'name', 'pk']

    def __str__(self):
        return self.name


class EventLayoutSnapshot(models.Model):
    """Editable event-specific copy: NOT public seat inventory."""
    event = models.OneToOneField(
        Event, on_delete=models.CASCADE, related_name='seating_layout',
    )
    template = models.ForeignKey(
        VenueLayoutTemplate, null=True, on_delete=models.SET_NULL,
        related_name='event_copies',
    )
    layout = models.JSONField(default=dict)
    version = models.PositiveIntegerField(default=1)
    assigned_by = models.ForeignKey(
        'accounts.User', null=True, on_delete=models.SET_NULL,
        related_name='venue_layouts_assigned',
    )
    updated_by = models.ForeignKey(
        'accounts.User', null=True, on_delete=models.SET_NULL,
        related_name='venue_event_layouts_edited',
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.event} — تخطيط مبدئي'


class VenueLayoutRevision(models.Model):
    """Immutable historical draft; restore creates a *new* current revision."""
    template = models.ForeignKey(
        VenueLayoutTemplate, null=True, blank=True,
        on_delete=models.CASCADE, related_name='revisions',
    )
    event_layout = models.ForeignKey(
        EventLayoutSnapshot, null=True, blank=True,
        on_delete=models.CASCADE, related_name='revisions',
    )
    version = models.PositiveIntegerField()
    layout = models.JSONField()
    created_by = models.ForeignKey(
        'accounts.User', null=True, on_delete=models.SET_NULL,
        related_name='venue_layout_revisions_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(template__isnull=False, event_layout__isnull=True) |
                           models.Q(template__isnull=True, event_layout__isnull=False)),
                name='layout_revision_one_owner',
            ),
            models.UniqueConstraint(
                fields=('template', 'version'), name='unique_venue_template_revision',
            ),
            models.UniqueConstraint(
                fields=('event_layout', 'version'), name='unique_venue_event_revision',
            ),
        ]
