import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = 'admin', 'مدير'
        CASHIER = 'cashier', 'كاشير'
        WAITER = 'waiter', 'نادل'
        KITCHEN = 'kitchen', 'مطبخ'

    class PreferredLanguage(models.TextChoices):
        ARABIC = 'ar', 'العربية'
        ENGLISH = 'en', 'English'

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    phone = models.CharField(max_length=30, unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.WAITER)
    preferred_language = models.CharField(max_length=5, choices=PreferredLanguage.choices, default=PreferredLanguage.ARABIC)

    def __str__(self):
        return self.get_full_name() or self.username


class StaffCapabilityOverride(models.Model):
    """Per-user allow/deny overrides layered on top of role defaults.

    Capability keys are defined centrally in ``accounts.permissions``.  Keeping
    the stored value as a short string makes the permission matrix extensible
    without a schema migration every time a new capability is introduced.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='staff_capability_overrides',
    )
    capability = models.CharField(max_length=80)
    allowed = models.BooleanField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['user_id', 'capability']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'capability'],
                name='unique_staff_capability_override',
            ),
        ]

    def __str__(self):
        state = 'allow' if self.allowed else 'deny'
        return f'{self.user}: {self.capability} ({state})'
