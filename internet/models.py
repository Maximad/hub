import uuid
from django.db import models


class WifiNetwork(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    name_ar = models.CharField(max_length=120)
    ssid = models.CharField(max_length=120)
    password = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    visible_on_qr = models.BooleanField(default=True)
    show_password_on_qr = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name_ar or self.ssid
