import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models


class AccessProfile(models.Model):
    """Reusable baseline permission profile for Hub staff accounts.

    Job titles stay descriptive and human-facing. Access profiles define what the
    account can do. base_role is only a compatibility/routing family for legacy
    notification and operational code.
    """

    class BaseRole(models.TextChoices):
        STAFF = 'staff', 'فريق عام'
        ADMIN = 'admin', 'إدارة'
        CASHIER = 'cashier', 'تشغيل وكاشير'
        WAITER = 'waiter', 'خدمة وتنظيم'
        KITCHEN = 'kitchen', 'تحضير وتشغيل خلفي'
        BARTENDER = 'bartender', 'بار وتحضير'
        INTERNET_PROVIDER = 'internet_provider', 'مزوّد إنترنت خارجي'

    code = models.SlugField(max_length=64, unique=True)
    name_ar = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    base_role = models.CharField(
        max_length=20,
        choices=BaseRole.choices,
        default=BaseRole.STAFF,
    )
    capabilities = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    is_system = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('name_ar', 'code')

    def __str__(self):
        return self.name_ar


class User(AbstractUser):
    class Role(models.TextChoices):
        STAFF = 'staff', 'فريق عام'
        ADMIN = 'admin', 'مدير'
        CASHIER = 'cashier', 'كاشير'
        WAITER = 'waiter', 'نادل'
        KITCHEN = 'kitchen', 'مطبخ'
        BARTENDER = 'bartender', 'بار'
        INTERNET_PROVIDER = 'internet_provider', 'مزوّد الإنترنت'

    class PreferredLanguage(models.TextChoices):
        ARABIC = 'ar', 'العربية'
        ENGLISH = 'en', 'English'

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    phone = models.CharField(max_length=30, unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.WAITER)
    job_title = models.CharField(max_length=120, blank=True)
    access_profile = models.ForeignKey(
        AccessProfile,
        on_delete=models.PROTECT,
        related_name='users',
        null=True,
        blank=True,
    )
    preferred_language = models.CharField(max_length=5, choices=PreferredLanguage.choices, default=PreferredLanguage.ARABIC)

    @property
    def access_label(self):
        if self.access_profile_id:
            return self.access_profile.name_ar
        return self.get_role_display()

    def __str__(self):
        return self.get_full_name() or self.username


class UserCapabilityOverride(models.Model):
    """Per-user allow/deny override for one Hub staff capability.

    Role rules remain the default policy. An override only records exceptions for
    a specific user so operations, navigation and notification delivery can all
    resolve the same effective capability set.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='capability_overrides',
    )
    capability = models.CharField(max_length=64)
    allowed = models.BooleanField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('user', 'capability'),
                name='unique_user_capability_override',
            ),
        ]
        ordering = ('capability',)

    def __str__(self):
        state = 'allow' if self.allowed else 'deny'
        return f'{self.user} / {self.capability} / {state}'
