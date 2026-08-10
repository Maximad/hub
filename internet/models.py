import uuid
from django.db import models


class WifiNetwork(models.Model):
    NETWORK_TYPES = [('free', 'مجانية'), ('paid', 'مدفوعة'), ('staff', 'موظفون')]
    NETWORK_BACKENDS = [('manual', 'يدوي'), ('mikrotik', 'MikroTik')]
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    name_ar = models.CharField(max_length=120)
    ssid = models.CharField(max_length=120)
    password = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    network_type = models.CharField(max_length=12, choices=NETWORK_TYPES, default='paid')
    network_backend = models.CharField(max_length=20, choices=NETWORK_BACKENDS, default='manual')
    bandwidth_profile = models.ForeignKey('core.InternetBandwidthProfile', on_delete=models.SET_NULL, null=True, blank=True)
    visible_on_qr = models.BooleanField(default=True)
    show_password_on_qr = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name_ar or self.ssid
