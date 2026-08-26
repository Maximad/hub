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


class InternetCatalogBinding(models.Model):
    """Stable storefront identity for an InternetPackage.

    InternetPackage remains authoritative for commercial/access policy. Product is
    only the catalog/cart representation used by the normal Hub ordering flow.
    """

    package = models.OneToOneField(
        'core.InternetPackage',
        on_delete=models.CASCADE,
        related_name='catalog_binding',
    )
    product = models.OneToOneField(
        'core.Product',
        on_delete=models.PROTECT,
        related_name='internet_catalog_binding',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.package} → {self.product}'


class InternetSessionNetworkState(models.Model):
    """Sensitive/durable network state for a package-less InternetSession.

    InternetSession keeps the public operational fields (provider, RouterOS identity,
    network status). This companion row keeps encrypted credentials and retry
    diagnostics out of the commercial session model. ``network_activated_at`` is
    the billing gate for network-managed metered sessions: time is never charged
    before the first successful provision.
    """

    session = models.OneToOneField(
        'core.InternetSession',
        on_delete=models.CASCADE,
        related_name='network_state',
    )
    network_credential_encrypted = models.TextField(blank=True, editable=False)
    network_activated_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_network_sync_at = models.DateTimeField(null=True, blank=True)
    last_network_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Network state for session {self.session_id}'


class InternetSessionNetworkOperation(models.Model):
    """Durable, idempotent network side effect owned by an InternetSession."""

    class Operation(models.TextChoices):
        PROVISION = 'provision', 'Provision'
        REFRESH = 'refresh', 'Refresh'
        DISCONNECT = 'disconnect', 'Disconnect'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PROCESSING = 'processing', 'Processing'
        SUCCEEDED = 'succeeded', 'Succeeded'
        FAILED = 'failed', 'Failed'

    session = models.ForeignKey(
        'core.InternetSession',
        on_delete=models.PROTECT,
        related_name='session_network_operations',
    )
    operation = models.CharField(max_length=20, choices=Operation.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    idempotency_key = models.CharField(max_length=180, unique=True)
    reason = models.CharField(max_length=200, blank=True)
    attempt_count = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(
                fields=('status', 'next_attempt_at'),
                name='internet_sess_netop_ready_idx',
            ),
        ]

    def __str__(self):
        return f'{self.session_id} — {self.operation} — {self.status}'
