import uuid
from django.db import models, transaction
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
    catalog_product = models.OneToOneField(
        'core.Product', on_delete=models.PROTECT, null=True, blank=True,
        related_name='membership_plan',
        help_text='Stable hidden catalog identity used for commercial membership sales.',
    )
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
    benefit_snapshot = models.JSONField(default=list, blank=True, editable=False)
    sale_idempotency_key = models.CharField(max_length=120, null=True, blank=True, unique=True)
    sale_request_fingerprint = models.CharField(max_length=64, blank=True, editable=False)
    is_complimentary = models.BooleanField(default=False, editable=False)
    activation_error = models.TextField(blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(gross_amount_syp__isnull=True) | Q(gross_amount_syp__gte=0),
                name='membership_subscription_gross_nonnegative',
            ),
            models.CheckConstraint(
                condition=Q(ends_at__isnull=True) | Q(ends_at__gt=models.F('starts_at')),
                name='membership_subscription_dates_valid',
            ),
        ]

    def __str__(self):
        return f'{self.member} — {self.plan} — {self.get_status_display()}'

    def save(self, *args, **kwargs):
        if self._state.adding and self.gross_amount_syp is None and self.plan_id:
            self.gross_amount_syp = self.plan.price_syp
        if self._state.adding and not self.benefit_snapshot and self.plan_id:
            self.benefit_snapshot = [rule.as_snapshot() for rule in self.plan.benefit_rules.filter(is_active=True)]
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
        # Status and the complete commercial bundle are one database unit. Network
        # callbacks registered by provisioning cannot run until this outer unit commits.
        with transaction.atomic():
            locked = type(self).objects.select_for_update().get(pk=self.pk)
            at = at or timezone.now()
            locked.status = self.Status.ACTIVE
            locked.activated_at = locked.activated_at or at
            locked.frozen_at = None
            locked.freeze_until = None
            locked.save(update_fields=['status', 'activated_at', 'frozen_at', 'freeze_until', 'updated_at'])
            from members.internet_benefits import provision_subscription_internet
            provision_subscription_internet(locked)
        self.refresh_from_db()

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
        from members.internet_benefits import invalidate_subscription_internet
        invalidate_subscription_internet(self, reason=reason)


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
    class BenefitType(models.TextChoices):
        PRODUCT_DISCOUNT_FIXED = 'product_discount_fixed', 'خصم ثابت على منتج'
        PRODUCT_DISCOUNT_PERCENT = 'product_discount_percent', 'نسبة خصم على منتج'
        EVENT_DISCOUNT_PERCENT = 'event_discount_percent', 'نسبة خصم على فعالية'
        BOOKING_PRIORITY = 'booking_priority', 'أولوية الحجز'
        WORKSPACE_MINUTES = 'workspace_minutes', 'دقائق مساحة العمل'
        INTERNET_MINUTES = 'internet_minutes', 'دقائق الإنترنت'
        INTERNET_MEMBER_PRICE = 'internet_member_price', 'سعر إنترنت للأعضاء'
        FREE_PRODUCT_QUANTITY = 'free_product_quantity', 'كمية منتج مجانية'
        EARLY_ACCESS = 'early_access', 'وصول مبكر'

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    plan = models.ForeignKey(MembershipPlan, on_delete=models.CASCADE, related_name='benefit_rules')
    benefit_type = models.CharField(max_length=50, choices=BenefitType.choices, blank=True)
    value_decimal = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    value_integer = models.IntegerField(null=True, blank=True)
    value_text = models.CharField(max_length=255, blank=True)
    scope_type = models.CharField(max_length=40, blank=True)
    scope_code = models.CharField(max_length=120, blank=True)
    usage_limit = models.PositiveIntegerField(null=True, blank=True)
    usage_period = models.CharField(max_length=30, blank=True)
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
    metadata = models.JSONField(default=dict, blank=True)
    internet_bandwidth_profile = models.ForeignKey(
        'core.InternetBandwidthProfile', on_delete=models.PROTECT, null=True, blank=True,
        related_name='membership_benefit_rules')
    max_concurrent_devices = models.PositiveSmallIntegerField(null=True, blank=True)
    max_registered_devices = models.PositiveSmallIntegerField(null=True, blank=True)
    commercial_allocation_syp = models.PositiveBigIntegerField(null=True, blank=True)
    complimentary_partner_service = models.BooleanField(
        default=False,
        help_text='Explicitly exempts a complimentary Internet benefit from partner revenue share.')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        target = self.product or self.category or self.menu_section or self.tag or self.item_type or 'قاعدة عامة'
        return f'{self.plan} — {target}'

    def as_snapshot(self):
        """Return the immutable, JSON-safe benefit definition stored on a subscription."""
        return {
            'rule_id': self.pk, 'benefit_type': self.benefit_type,
            'value_decimal': str(self.value_decimal) if self.value_decimal is not None else None,
            'value_integer': self.value_integer, 'value_text': self.value_text,
            'scope_type': self.scope_type, 'scope_code': self.scope_code,
            'usage_limit': self.usage_limit, 'usage_period': self.usage_period,
            'priority': self.priority, 'metadata': self.metadata,
            'internet_bandwidth_profile_id': self.internet_bandwidth_profile_id,
            'internet_bandwidth_profile_code': (
                self.internet_bandwidth_profile.code if self.internet_bandwidth_profile_id else ''),
            'max_concurrent_devices': self.max_concurrent_devices,
            'max_registered_devices': self.max_registered_devices,
            'commercial_allocation_syp': self.commercial_allocation_syp,
            'complimentary_partner_service': self.complimentary_partner_service,
            # Stable compatibility targets for benefit rules created before the generic layer.
            'product_id': self.product_id, 'category_id': self.category_id,
            'menu_section_id': self.menu_section_id, 'tag_id': self.tag_id,
            'item_type': self.item_type, 'beverage_type': self.beverage_type,
            'food_type': self.food_type, 'service_type': self.service_type,
            'applies_to_alcohol': self.applies_to_alcohol,
            'included_minutes': self.included_minutes,
        }


class CommercialAllocation(models.Model):
    """Immutable commercial component snapshot for one membership sale."""
    class ComponentType(models.TextChoices):
        MEMBERSHIP = 'membership', 'Membership'
        INTERNET = 'internet', 'Internet'
        WORKSPACE = 'workspace', 'Workspace'
        OTHER = 'other', 'Other'

    subscription = models.ForeignKey(MembershipSubscription, on_delete=models.PROTECT, related_name='commercial_allocations')
    component_type = models.CharField(max_length=20, choices=ComponentType.choices)
    source_benefit_rule_id = models.PositiveBigIntegerField(null=True, blank=True)
    allocated_amount_syp = models.PositiveBigIntegerField()
    internet_entitlement = models.OneToOneField('core.InternetEntitlement', on_delete=models.PROTECT, null=True, blank=True, related_name='commercial_allocation')
    partner = models.ForeignKey('core.InternetPartner', on_delete=models.PROTECT, null=True, blank=True, related_name='commercial_allocations')
    partner_name_snapshot = models.CharField(max_length=120, blank=True)
    partner_share_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    partner_share_amount_syp = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('subscription', 'component_type', 'source_benefit_rule_id'), name='unique_subscription_commercial_component'),
            models.UniqueConstraint(
                fields=('subscription',),
                condition=Q(component_type='membership', source_benefit_rule_id__isnull=True),
                name='unique_subscription_residual_membership'),
        ]

    def clean(self):
        super().clean()
        gross = self.subscription.gross_amount_syp
        existing = type(self).objects.filter(subscription=self.subscription).exclude(pk=self.pk).aggregate(total=models.Sum('allocated_amount_syp'))['total'] or 0
        if gross is not None and existing + self.allocated_amount_syp > gross:
            raise ValidationError({'allocated_amount_syp': 'Component allocations cannot exceed subscription gross amount.'})
        if self.component_type != self.ComponentType.INTERNET and (self.partner_id or self.partner_share_percent is not None):
            raise ValidationError('Partner share applies only to the Internet component.')

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('Commercial allocation snapshots are immutable.')
        self.full_clean()
        return super().save(*args, **kwargs)


class Program(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'مسودة'
        OPEN = 'open', 'مفتوح'
        COMPLETED = 'completed', 'مكتمل'
        CANCELLED = 'cancelled', 'ملغى'

    code = models.SlugField(max_length=80, unique=True)
    name_ar = models.CharField(max_length=160)
    name_en = models.CharField(max_length=160, blank=True)
    description_ar = models.TextField(blank=True)
    description_en = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name_ar or self.name_en or self.code

    def clean(self):
        super().clean()
        if self.ends_at and self.starts_at and self.ends_at <= self.starts_at:
            raise ValidationError({'ends_at': 'يجب أن يكون وقت النهاية بعد وقت البداية.'})


class ProgramEnrollment(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'قيد الانتظار'
        ACTIVE = 'active', 'نشط'
        COMPLETED = 'completed', 'مكتمل'
        CANCELLED = 'cancelled', 'ملغى'

    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name='enrollments')
    member = models.ForeignKey('core.Member', on_delete=models.PROTECT, null=True, blank=True, related_name='program_enrollments')
    participant_name = models.CharField(max_length=160, blank=True)
    participant_metadata = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    subscription = models.ForeignKey(MembershipSubscription, on_delete=models.SET_NULL, null=True, blank=True, related_name='program_enrollments')
    order = models.ForeignKey('core.Order', on_delete=models.SET_NULL, null=True, blank=True, related_name='program_enrollments')
    payment = models.ForeignKey('core.Payment', on_delete=models.SET_NULL, null=True, blank=True, related_name='program_enrollments')
    enrolled_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.program} — {self.member or self.participant_name}'

    def clean(self):
        super().clean()
        errors = {}
        if not self.member_id and not self.participant_name.strip():
            errors['participant_name'] = 'الاسم مطلوب عند عدم ربط المشارك بعضو.'
        if self.subscription_id and self.member_id and self.subscription.member_id != self.member_id:
            errors['subscription'] = 'يجب أن يعود الاشتراك للعضو المحدد.'
        if errors:
            raise ValidationError(errors)

    def complete(self, at=None):
        self.status = self.Status.COMPLETED
        self.completed_at = at or timezone.now()
        self.save(update_fields=['status', 'completed_at', 'updated_at'])

    def cancel(self, at=None):
        self.status = self.Status.CANCELLED
        self.cancelled_at = at or timezone.now()
        self.save(update_fields=['status', 'cancelled_at', 'updated_at'])


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
