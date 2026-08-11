import uuid
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone


class MembershipPlan(models.Model):
    class BillingPeriod(models.TextChoices):
        MONTHLY = 'monthly', 'Monthly'
        ANNUAL = 'annual', 'Annual'
        FIXED_TERM = 'fixed_term', 'Fixed term'
        NONE = 'none', 'None'

    class TermUnit(models.TextChoices):
        DAY = 'day', 'Day(s)'
        WEEK = 'week', 'Week(s)'
        MONTH = 'month', 'Month(s)'
        YEAR = 'year', 'Year(s)'

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    code = models.SlugField(unique=True)
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    description_ar = models.TextField(blank=True)
    description_en = models.TextField(blank=True)
    billing_period = models.CharField(max_length=20, choices=BillingPeriod.choices, default=BillingPeriod.NONE)
    term_value = models.PositiveIntegerField(null=True, blank=True)
    term_unit = models.CharField(max_length=10, choices=TermUnit.choices, null=True, blank=True)
    price_syp = models.PositiveBigIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    visible_to_staff = models.BooleanField(default=True)
    visible_to_customer = models.BooleanField(default=False)
    sort_order = models.IntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name_ar or self.name_en or self.code


class MembershipSubscription(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        ACTIVE = 'active', 'Active'
        FROZEN = 'frozen', 'Frozen'
        EXPIRED = 'expired', 'Expired'
        CANCELLED = 'cancelled', 'Cancelled'

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    member = models.ForeignKey('core.Member', on_delete=models.CASCADE, related_name='subscriptions')
    plan = models.ForeignKey(MembershipPlan, on_delete=models.PROTECT, related_name='subscriptions')
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    activated_at = models.DateTimeField(null=True, blank=True)
    frozen_at = models.DateTimeField(null=True, blank=True)
    freeze_until = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    order = models.ForeignKey('core.Order', on_delete=models.SET_NULL, null=True, blank=True, related_name='membership_subscriptions')
    payment = models.ForeignKey('core.Payment', on_delete=models.SET_NULL, null=True, blank=True, related_name='membership_subscriptions')
    gross_amount_syp = models.PositiveBigIntegerField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_membership_subscriptions')
    remaining_internet_minutes = models.IntegerField(null=True, blank=True)
    remaining_credit_syp = models.IntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.member} — {self.plan} — {self.get_status_display()}'

    def save(self, *args, **kwargs):
        if self._state.adding and self.gross_amount_syp is None and self.plan_id:
            self.gross_amount_syp = self.plan.price_syp
        super().save(*args, **kwargs)

    def effective_status(self, at=None):
        at = at or timezone.now()
        if self.status == self.Status.CANCELLED:
            return self.Status.CANCELLED
        if self.ends_at is not None and at >= self.ends_at:
            return self.Status.EXPIRED
        if at < self.starts_at:
            return self.Status.PENDING
        if self.status == self.Status.EXPIRED:
            return self.Status.EXPIRED
        if self.status == self.Status.FROZEN and (self.freeze_until is None or at < self.freeze_until):
            return self.Status.FROZEN
        return self.Status.ACTIVE if self.status in {self.Status.ACTIVE, self.Status.FROZEN} else self.status

    def is_active_at(self, at=None):
        return self.effective_status(at) == self.Status.ACTIVE

    def activate(self, at=None):
        at = at or timezone.now()
        self.status = self.Status.ACTIVE
        self.activated_at = self.activated_at or at
        self.frozen_at = None
        self.freeze_until = None
        self.save(update_fields=['status', 'activated_at', 'frozen_at', 'freeze_until', 'updated_at'])

    def freeze(self, until=None, at=None):
        at = at or timezone.now()
        if until is not None and until <= at:
            raise ValidationError({'freeze_until': 'Freeze end must be after freeze start.'})
        self.status = self.Status.FROZEN
        self.frozen_at = at
        self.freeze_until = until
        self.save(update_fields=['status', 'frozen_at', 'freeze_until', 'updated_at'])

    def unfreeze(self):
        self.status = self.Status.ACTIVE
        self.frozen_at = None
        self.freeze_until = None
        self.save(update_fields=['status', 'frozen_at', 'freeze_until', 'updated_at'])

    def cancel(self, reason='', at=None):
        self.status = self.Status.CANCELLED
        self.cancelled_at = at or timezone.now()
        self.cancellation_reason = reason
        self.save(update_fields=['status', 'cancelled_at', 'cancellation_reason', 'updated_at'])


class MemberAttribute(models.Model):
    member = models.ForeignKey('core.Member', on_delete=models.CASCADE, related_name='attributes')
    code = models.SlugField(max_length=80)
    label_ar = models.CharField(max_length=120, blank=True)
    granted_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='granted_member_attributes')
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('member_id', 'code', '-granted_at')
        constraints = [
            models.UniqueConstraint(fields=('member', 'code'), condition=Q(expires_at__isnull=True), name='unique_permanent_member_attribute'),
        ]

    def __str__(self):
        return f'{self.member} — {self.label_ar or self.code}'

    def is_active_at(self, at=None):
        at = at or timezone.now()
        return self.granted_at <= at and (self.expires_at is None or at < self.expires_at)

    def clean(self):
        super().clean()
        if self.expires_at is not None and self.expires_at <= self.granted_at:
            raise ValidationError({'expires_at': 'Expiry must be after the grant time.'})
        overlaps = type(self).objects.filter(member=self.member, code=self.code).exclude(pk=self.pk)
        overlaps = overlaps.filter(Q(expires_at__isnull=True) | Q(expires_at__gt=self.granted_at))
        if self.expires_at is not None:
            overlaps = overlaps.filter(granted_at__lt=self.expires_at)
        if overlaps.exists():
            raise ValidationError('This member already has an overlapping attribute with this code.')

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class MembershipBenefitRule(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    plan = models.ForeignKey(MembershipPlan, on_delete=models.CASCADE, related_name='benefit_rules')
    item_type = models.CharField(max_length=30, blank=True)
    beverage_type = models.CharField(max_length=30, blank=True)
    food_type = models.CharField(max_length=30, blank=True)
    service_type = models.CharField(max_length=30, blank=True)
    menu_section = models.ForeignKey('catalog.MenuSection', on_delete=models.SET_NULL, null=True, blank=True)
    category = models.ForeignKey('core.Category', on_delete=models.SET_NULL, null=True, blank=True)
    product = models.ForeignKey('core.Product', on_delete=models.SET_NULL, null=True, blank=True)
    tag = models.ForeignKey('catalog.Tag', on_delete=models.SET_NULL, null=True, blank=True)
    discount_percent = models.IntegerField(null=True, blank=True)
    discount_amount_syp = models.IntegerField(null=True, blank=True)
    included_quantity = models.IntegerField(null=True, blank=True)
    included_minutes = models.IntegerField(null=True, blank=True)
    monthly_credit_syp = models.IntegerField(null=True, blank=True)
    applies_to_alcohol = models.BooleanField(default=False)
    priority = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        target = self.product or self.category or self.menu_section or self.tag or self.item_type or 'قاعدة عامة'
        return f'{self.plan} — {target}'


class MemberCreditLedger(models.Model):
    CHANGE_TYPES = [('add_minutes','Add Minutes'),('use_minutes','Use Minutes'),('add_credit','Add Credit'),('use_credit','Use Credit'),('manual_adjustment','Manual Adjustment')]
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    member = models.ForeignKey('core.Member', on_delete=models.CASCADE, related_name='credit_ledger')
    subscription = models.ForeignKey(MembershipSubscription, on_delete=models.SET_NULL, null=True, blank=True)
    change_type = models.CharField(max_length=30, choices=CHANGE_TYPES)
    minutes_delta = models.IntegerField(null=True, blank=True)
    credit_delta_syp = models.IntegerField(null=True, blank=True)
    related_order = models.ForeignKey('core.Order', on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        delta = self.minutes_delta if self.minutes_delta is not None else self.credit_delta_syp
        return f'{self.member} — {self.change_type} — {delta}'


class MemberActivationToken(models.Model):
    """A short-lived, one-time credential. Only its SHA-256 digest is persisted."""
    member = models.ForeignKey('core.Member', on_delete=models.CASCADE, related_name='activation_tokens')
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class MemberDeviceToken(models.Model):
    """Revocable browser recognition credential; the raw secret is cookie-only."""
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    member = models.ForeignKey('core.Member', on_delete=models.CASCADE, related_name='device_tokens')
    token_hash = models.CharField(max_length=64, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    device_label = models.CharField(max_length=120, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['uuid', 'token_hash'], name='unique_member_device_secret')]
