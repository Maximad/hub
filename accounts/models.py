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
