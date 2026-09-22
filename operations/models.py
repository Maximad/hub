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
    closing_started_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    reopened_at = models.DateTimeField(null=True, blank=True)
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='opened_business_days',
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


class StaffDailyCodeReceipt(models.Model):
    business_day = models.ForeignKey(BusinessDay, on_delete=models.CASCADE, related_name='code_receipts')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='daily_code_receipts')
    code_version = models.PositiveIntegerField(default=1)
    notified_at = models.DateTimeField(null=True, blank=True)
    viewed_at = models.DateTimeField(null=True, blank=True)
    first_used_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    use_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['user__username']
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
