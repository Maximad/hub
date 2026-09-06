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
