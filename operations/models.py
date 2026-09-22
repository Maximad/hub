from django.conf import settings
from django.db import models
from django.db.models import Q


class BusinessDay(models.Model):
    class Status(models.TextChoices):
        OPEN = 'open', 'مفتوح'
        CLOSING = 'closing', 'قيد الإغلاق'
        CLOSED = 'closed', 'مغلق'
        REOPENED = 'reopened', 'أعيد فتحه'

    business_date = models.DateField(unique=True, verbose_name='تاريخ العمل')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    cutoff_hour = models.PositiveSmallIntegerField(default=4, verbose_name='ساعة نهاية يوم العمل')
    opened_at = models.DateTimeField(null=True, blank=True)
    opening_completed_at = models.DateTimeField(null=True, blank=True)
    opening_last_reminded_at = models.DateTimeField(null=True, blank=True)
    closing_started_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    reopened_at = models.DateTimeField(null=True, blank=True)
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='opened_business_days',
    )
    opening_completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='completed_business_day_openings',
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='closed_business_days',
    )
    reopened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reopened_business_days',
    )
    reopen_reason = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    code_hash = models.CharField(max_length=256, blank=True, editable=False)
    code_ciphertext = models.TextField(blank=True, editable=False)
    code_version = models.PositiveIntegerField(default=0, editable=False)
    code_issued_at = models.DateTimeField(null=True, blank=True, editable=False)
    closing_snapshot = models.JSONField(default=dict, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-business_date']
        permissions = [
            ('manage_business_day', 'Can manage business day'),
            ('view_daily_staff_code', 'Can view daily staff code'),
            ('rotate_daily_staff_code', 'Can rotate daily staff code'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(cutoff_hour__gte=0, cutoff_hour__lte=23),
                name='business_day_cutoff_hour_range',
            ),
        ]

    def __str__(self):
        return f'{self.business_date} — {self.get_status_display()}'


class BusinessDayStaffAssignment(models.Model):
    business_day = models.ForeignKey(BusinessDay, on_delete=models.CASCADE, related_name='staff_assignments')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='business_day_assignments')
    role_snapshot = models.CharField(max_length=40, blank=True)
    note = models.CharField(max_length=240, blank=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='assigned_business_day_staff',
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['user_id']
        constraints = [
            models.UniqueConstraint(fields=['business_day', 'user'], name='unique_business_day_staff_assignment'),
        ]

    def __str__(self):
        return f'{self.business_day.business_date} — {self.user}'


class BusinessDayChecklistItem(models.Model):
    class Stage(models.TextChoices):
        OPENING = 'opening', 'الافتتاح'

    class Status(models.TextChoices):
        PENDING = 'pending', 'بانتظار التأكيد'
        DONE = 'done', 'تم'
        WAIVED = 'waived', 'تم التجاوز بسبب مسجّل'

    business_day = models.ForeignKey(BusinessDay, on_delete=models.CASCADE, related_name='checklist_items')
    stage = models.CharField(max_length=20, choices=Stage.choices, default=Stage.OPENING)
    code = models.CharField(max_length=80)
    label_ar = models.CharField(max_length=240)
    is_required = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    note = models.TextField(blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='completed_business_day_checklist_items',
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['stage', 'sort_order', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['business_day', 'stage', 'code'], name='unique_business_day_checklist_code'
            ),
        ]

    def __str__(self):
        return f'{self.business_day.business_date} — {self.label_ar}'


class HandoverNote(models.Model):
    class Priority(models.TextChoices):
        NORMAL = 'normal', 'عادية'
        HIGH = 'high', 'مهمة'

    class Status(models.TextChoices):
        OPEN = 'open', 'تحتاج متابعة'
        ACKNOWLEDGED = 'acknowledged', 'تم الاطلاع'
        RESOLVED = 'resolved', 'تم الحل'

    business_day = models.ForeignKey(BusinessDay, on_delete=models.CASCADE, related_name='handover_notes')
    message = models.TextField()
    priority = models.CharField(max_length=12, choices=Priority.choices, default=Priority.NORMAL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='assigned_handover_notes',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_handover_notes',
    )
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='acknowledged_handover_notes',
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='resolved_handover_notes',
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['status', '-priority', '-created_at']

    def __str__(self):
        return self.message[:80]


class OperationalTaskTemplate(models.Model):
    class Priority(models.TextChoices):
        NORMAL = 'normal', 'عادية'
        HIGH = 'high', 'مهمة'

    title_ar = models.CharField(max_length=240)
    details = models.TextField(blank=True)
    weekdays = models.JSONField(default=list, blank=True, help_text='0=Monday ... 6=Sunday; empty means every day')
    due_time = models.TimeField(null=True, blank=True)
    priority = models.CharField(max_length=12, choices=Priority.choices, default=Priority.NORMAL)
    responsibility_role = models.CharField(max_length=40, blank=True)
    is_required = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    active_from = models.DateField(null=True, blank=True)
    active_until = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_operational_task_templates',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['title_ar', 'pk']

    def __str__(self):
        return self.title_ar


class BusinessDayTask(models.Model):
    class Kind(models.TextChoices):
        RECURRING = 'recurring', 'مهمة دورية'
        EVENT = 'event', 'تحضير فعالية'
        RESERVATION = 'reservation', 'تحضير حجز'
        INVENTORY = 'inventory', 'مخزون/شراء'
        MANUAL = 'manual', 'مهمة يدوية'

    class Status(models.TextChoices):
        PENDING = 'pending', 'مفتوحة'
        DONE = 'done', 'تمت'
        WAIVED = 'waived', 'أُغلقت بسبب مسجّل'

    business_day = models.ForeignKey(BusinessDay, on_delete=models.CASCADE, related_name='tasks')
    fingerprint = models.CharField(max_length=180)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.MANUAL)
    title_ar = models.CharField(max_length=240)
    details = models.TextField(blank=True)
    priority = models.CharField(max_length=12, choices=OperationalTaskTemplate.Priority.choices, default=OperationalTaskTemplate.Priority.NORMAL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    due_at = models.DateTimeField(null=True, blank=True)
    is_required = models.BooleanField(default=False)
    responsibility_role = models.CharField(max_length=40, blank=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='assigned_business_day_tasks',
    )
    task_template = models.ForeignKey(
        OperationalTaskTemplate, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='generated_tasks',
    )
    source_type = models.CharField(max_length=40, blank=True)
    source_id = models.CharField(max_length=80, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_business_day_tasks',
    )
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='completed_business_day_tasks',
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    completion_note = models.TextField(blank=True)
    last_reminded_at = models.DateTimeField(null=True, blank=True)
    reminder_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['status', 'due_at', '-priority', 'created_at']
        constraints = [
            models.UniqueConstraint(fields=['business_day', 'fingerprint'], name='unique_business_day_task_fingerprint'),
        ]
        indexes = [
            models.Index(fields=['business_day', 'status', 'due_at'], name='business_day_task_due_idx'),
        ]

    def __str__(self):
        return f'{self.business_day.business_date} — {self.title_ar}'


class StaffDailyCodeReceipt(models.Model):
    business_day = models.ForeignKey(BusinessDay, on_delete=models.CASCADE, related_name='code_receipts')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='daily_code_receipts')
    code_version = models.PositiveIntegerField(default=1)
    notified_at = models.DateTimeField(null=True, blank=True)
    viewed_at = models.DateTimeField(null=True, blank=True)
    first_used_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_reminded_at = models.DateTimeField(null=True, blank=True)
    reminder_count = models.PositiveIntegerField(default=0)
    use_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['user_id']
        constraints = [
            models.UniqueConstraint(
                fields=['business_day', 'user'],
                name='unique_daily_code_receipt_per_user',
            ),
        ]

    def __str__(self):
        return f'{self.business_day.business_date} — {self.user}'


class BusinessDayException(models.Model):
    class Severity(models.TextChoices):
        INFO = 'info', 'معلومة'
        WARNING = 'warning', 'تحذير'
        BLOCKER = 'blocker', 'مانع للإغلاق'

    class Status(models.TextChoices):
        OPEN = 'open', 'مفتوح'
        RESOLVED = 'resolved', 'تم الحل'
        CARRIED_FORWARD = 'carried_forward', 'رُحّل'

    class Category(models.TextChoices):
        ORDER = 'order', 'طلبات'
        INTERNET = 'internet', 'إنترنت'
        VISIT = 'visit', 'زيارات'
        SHIFT = 'shift', 'مناوبات'
        FINANCE = 'finance', 'مالية'
        SYSTEM = 'system', 'نظام'

    business_day = models.ForeignKey(BusinessDay, on_delete=models.CASCADE, related_name='exceptions')
    fingerprint = models.CharField(max_length=180)
    category = models.CharField(max_length=20, choices=Category.choices)
    severity = models.CharField(max_length=20, choices=Severity.choices, default=Severity.WARNING)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    object_type = models.CharField(max_length=80, blank=True)
    object_id = models.CharField(max_length=80, blank=True)
    title_ar = models.CharField(max_length=240)
    details = models.JSONField(default=dict, blank=True)
    resolution_note = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='resolved_business_day_exceptions',
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-severity', 'category', 'created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['business_day', 'fingerprint'],
                name='unique_business_day_exception_fingerprint',
            ),
        ]

    def __str__(self):
        return self.title_ar
