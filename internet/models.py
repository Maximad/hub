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


class InternetSessionBrowserBinding(models.Model):
    """Identify the visit browser/device that owns one customer Internet session.

    The raw visit cookie is never stored here.  The binding points to the already
    hashed/revocable HubVisitBrowserCredential created when the browser selected or
    joined its table bill.  Historical staff/manual InternetSession rows may remain
    unbound; only customer self-service sessions created after this model is deployed
    are required to have a binding.
    """

    session = models.OneToOneField(
        'core.InternetSession',
        on_delete=models.CASCADE,
        related_name='browser_binding',
    )
    credential = models.ForeignKey(
        'core.HubVisitBrowserCredential',
        on_delete=models.CASCADE,
        related_name='internet_session_bindings',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=('credential', 'created_at'),
                name='internet_browser_cred_idx',
            ),
        ]

    def __str__(self):
        return f'Session {self.session_id} → browser credential {self.credential_id}'


class InternetSessionNetworkState(models.Model):
    """Sensitive/durable network state for a package-less InternetSession.

    InternetSession keeps the public operational fields (provider, RouterOS identity,
    network status). This companion row keeps encrypted credentials and retry
    diagnostics out of the commercial session model. ``network_activated_at`` is
    the billing gate for network-managed metered sessions and the activation gate
    for complimentary guest sessions: time never starts before provisioning works.
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


class InternetOperationsState(models.Model):
    """Singleton-style operational heartbeat for the Internet subsystem.

    This row contains only non-secret health metadata. It lets the staff console
    distinguish an idle worker from a dead worker and persist the latest read-only
    MikroTik connectivity check without exposing credentials.
    """

    key = models.CharField(max_length=40, unique=True, default='default')
    last_worker_seen_at = models.DateTimeField(null=True, blank=True)
    last_lifecycle_at = models.DateTimeField(null=True, blank=True)
    last_worker_summary = models.JSONField(default=dict, blank=True)
    last_worker_error = models.CharField(max_length=500, blank=True)
    last_mikrotik_check_at = models.DateTimeField(null=True, blank=True)
    last_mikrotik_check_ok = models.BooleanField(null=True, blank=True)
    last_mikrotik_check_message = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Internet operations state ({self.key})'


class GuestWifiPolicy(models.Model):
    """Compact venue policy for complimentary/basic captive-portal Internet.

    Identity and Internet tier remain separate. This policy only governs the slow,
    complimentary baseline used to keep an open SSID useful inside Hub without
    becoming neighbourhood Internet. Paid fast access and entitlements remain owned
    by the existing commercial Internet engine.
    """

    key = models.CharField(max_length=40, unique=True, default='default')
    enabled = models.BooleanField('تفعيل الإنترنت الأساسي', default=False)
    require_venue_code = models.BooleanField('طلب رمز المكان عند أول اتصال يومي', default=True)
    member_bypass_venue_code = models.BooleanField('الحساب ذو الاشتراك الفعال يتجاوز رمز المكان', default=True)
    session_minutes = models.PositiveSmallIntegerField(
        'الرصيد المجاني الأولي بالدقائق',
        default=120,
        help_text='يُمنح مرة واحدة للجهاز في يوم العمل عند أول تفعيل للإنترنت الأساسي.',
    )
    max_sessions_per_day = models.PositiveSmallIntegerField(
        default=1,
        help_text='حقل توافق قديم؛ السياسة الحالية تعتمد سقف الدقائق اليومي بدلاً من عدد الجلسات.',
    )
    daily_complimentary_minutes = models.PositiveSmallIntegerField(
        'السقف المجاني اليومي بالدقائق',
        default=360,
        help_text='يشمل الرصيد الأولي ومكافآت الطلبات والمنح اليدوية، ولا يقيّد الإنترنت المدفوع أو الاستحقاقات.',
    )
    order_bonus_enabled = models.BooleanField('تمديد الإنترنت الأساسي بعد طلب مؤهل', default=True)
    order_bonus_minutes = models.PositiveSmallIntegerField(
        'دقائق المكافأة لكل طلب مؤهل',
        default=120,
    )
    qualifying_order_minimum_syp = models.PositiveBigIntegerField(
        'الحد الأدنى لقيمة الطلب المؤهل',
        default=0,
        help_text='صفر يعني أن أي طلب مقبول بقيمة أكبر من صفر مؤهل.',
    )
    code_rotation_minutes = models.PositiveSmallIntegerField('تغيير رمز المكان كل (دقيقة)', default=240)
    bandwidth_profile = models.ForeignKey(
        'core.InternetBandwidthProfile',
        verbose_name='ملف سرعة الإنترنت الأساسي',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='guest_wifi_policies',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Guest Wi-Fi policy ({self.key})'


class GuestWifiDailyAllowance(models.Model):
    """One browser/device's complimentary allowance for one Hub business date."""

    credential = models.ForeignKey(
        'core.HubVisitBrowserCredential',
        on_delete=models.CASCADE,
        related_name='guest_wifi_daily_allowances',
    )
    business_date = models.DateField(db_index=True)
    initial_minutes_granted = models.PositiveSmallIntegerField(default=0)
    order_bonus_minutes_granted = models.PositiveSmallIntegerField(default=0)
    manual_bonus_minutes_granted = models.PositiveSmallIntegerField(default=0)
    consumed_seconds = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('credential', 'business_date'),
                name='uniq_guest_wifi_allowance_per_day',
            ),
        ]
        indexes = [
            models.Index(
                fields=('business_date', 'credential'),
                name='guest_wifi_allow_day_idx',
            ),
        ]

    @property
    def total_granted_minutes(self):
        return (
            int(self.initial_minutes_granted or 0)
            + int(self.order_bonus_minutes_granted or 0)
            + int(self.manual_bonus_minutes_granted or 0)
        )

    def __str__(self):
        return f'Basic Wi-Fi allowance {self.credential_id} / {self.business_date}'


class GuestWifiGrant(models.Model):
    """Audit row for one complimentary/basic InternetSession on one browser."""

    credential = models.ForeignKey(
        'core.HubVisitBrowserCredential',
        on_delete=models.CASCADE,
        related_name='guest_wifi_grants',
    )
    session = models.OneToOneField(
        'core.InternetSession',
        on_delete=models.CASCADE,
        related_name='guest_wifi_grant',
    )
    allowance = models.ForeignKey(
        GuestWifiDailyAllowance,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='sessions',
    )
    business_date = models.DateField(db_index=True)
    code_slot = models.BigIntegerField(null=True, blank=True)
    usage_accounted_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=('credential', 'business_date'),
                name='guest_wifi_cred_day_idx',
            ),
        ]

    def __str__(self):
        return f'Guest Wi-Fi grant {self.session_id}'


class GuestWifiOrderBonus(models.Model):
    """Idempotent complimentary-time bonus created from one accepted Hub order."""

    order = models.OneToOneField(
        'core.Order',
        on_delete=models.CASCADE,
        related_name='guest_wifi_order_bonus',
    )
    allowance = models.ForeignKey(
        GuestWifiDailyAllowance,
        on_delete=models.CASCADE,
        related_name='order_bonuses',
    )
    credential = models.ForeignKey(
        'core.HubVisitBrowserCredential',
        on_delete=models.CASCADE,
        related_name='guest_wifi_order_bonuses',
    )
    business_date = models.DateField(db_index=True)
    minutes = models.PositiveSmallIntegerField()
    order_total_syp_snapshot = models.PositiveBigIntegerField(default=0)
    applied_session = models.ForeignKey(
        'core.InternetSession',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='guest_wifi_applied_order_bonuses',
    )
    applied_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(
                fields=('credential', 'business_date', 'revoked_at'),
                name='guest_wifi_bonus_day_idx',
            ),
        ]

    def __str__(self):
        return f'Order {self.order_id} → +{self.minutes} min basic Wi-Fi'


class GuestWifiCodeAttempt(models.Model):
    """Hashed abuse-control bucket; no raw client IP or venue code is stored."""

    fingerprint_hash = models.CharField(max_length=64, unique=True, editable=False)
    window_started_at = models.DateTimeField()
    attempt_count = models.PositiveSmallIntegerField(default=0)
    blocked_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Guest Wi-Fi attempts {self.fingerprint_hash[:10]}'
