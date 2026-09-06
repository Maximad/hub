import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class MemberAccount(models.Model):
    """Permanent member login identity, independent from subscription state."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        ACTIVE = 'active', 'Active'
        LOCKED = 'locked', 'Locked'

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    member = models.OneToOneField(
        'core.Member', on_delete=models.CASCADE, related_name='login_account'
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    email = models.EmailField(null=True, blank=True, unique=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.member} — {self.get_status_display()}'

    @property
    def is_claimed(self):
        return self.claimed_at is not None and self.status == self.Status.ACTIVE

    def mark_claimed(self, at=None):
        at = at or timezone.now()
        self.status = self.Status.ACTIVE
        self.claimed_at = self.claimed_at or at
        self.last_login_at = at
        self.save(update_fields=['status', 'claimed_at', 'last_login_at', 'updated_at'])


class MemberInvitation(models.Model):
    """Revocable, single-use invitation. Only the SHA-256 token digest is stored."""

    class Purpose(models.TextChoices):
        ACCOUNT_CLAIM = 'account_claim', 'Account claim'
        ADD_DEVICE = 'add_device', 'Add trusted device'

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    target_member = models.ForeignKey(
        'core.Member', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='account_invitations',
    )
    claimed_member = models.ForeignKey(
        'core.Member', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='claimed_account_invitations',
    )
    invited_phone = models.CharField(max_length=30, blank=True)
    invited_name = models.CharField(max_length=120, blank=True)
    purpose = models.CharField(
        max_length=20, choices=Purpose.choices, default=Purpose.ACCOUNT_CLAIM
    )
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    expires_at = models.DateTimeField()
    claimed_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_member_invitations',
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=('token_hash',), name='member_invite_token_idx'),
            models.Index(fields=('expires_at',), name='member_invite_expiry_idx'),
        ]

    def __str__(self):
        target = self.target_member or self.invited_phone or self.uuid
        return f'{target} — {self.get_purpose_display()}'

    def is_available(self, at=None):
        at = at or timezone.now()
        return not self.claimed_at and not self.revoked_at and self.expires_at > at


class MemberLoginChallenge(models.Model):
    """Short-lived passwordless phone verification challenge.

    Unknown phone numbers intentionally receive a member-less challenge so the
    public request flow does not reveal whether an account exists. Raw codes are
    never stored; phone numbers are represented only by a keyed digest.
    """

    class DeliveryStatus(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SENT = 'sent', 'Sent'
        SKIPPED = 'skipped', 'Skipped'
        FAILED = 'failed', 'Failed'

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    member = models.ForeignKey(
        'core.Member', on_delete=models.CASCADE, null=True, blank=True,
        related_name='login_challenges',
    )
    phone_hash = models.CharField(max_length=64, db_index=True)
    code_hash = models.CharField(max_length=64, editable=False)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    delivery_status = models.CharField(
        max_length=16, choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING
    )
    requested_ip_hash = models.CharField(max_length=64, blank=True, db_index=True)
    next_path = models.CharField(max_length=500, blank=True)
    user_agent = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=('member', 'created_at'), name='member_login_member_idx'),
            models.Index(fields=('expires_at',), name='member_login_expiry_idx'),
        ]

    @property
    def is_open(self):
        return (
            self.consumed_at is None
            and self.expires_at > timezone.now()
            and self.attempts < self.max_attempts
        )
