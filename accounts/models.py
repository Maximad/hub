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
