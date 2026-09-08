from django.conf import settings
from django.db import models


class IntegrationToken(models.Model):
    """High-entropy bearer credential for the Hub management API.

    Only a SHA-256 digest of the generated secret is stored. The complete
    credential is shown once by the creation command and cannot be recovered
    from the database afterward.
    """

    name = models.CharField(max_length=120)
    prefix = models.CharField(max_length=24, unique=True, db_index=True)
    secret_digest = models.CharField(max_length=64, editable=False)
    scopes = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_integration_tokens',
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'prefix']
        verbose_name = 'Management integration token'
        verbose_name_plural = 'Management integration tokens'

    def __str__(self):
        return f'{self.name} — {self.prefix}'


class IntegrationRequestLog(models.Model):
    """Metadata-only audit trail for management API calls.

    Request bodies and bearer credentials are intentionally never persisted.
    Business mutations continue to use core.ActivityLog for field-level
    before/after evidence.
    """

    token = models.ForeignKey(
        IntegrationToken,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='request_logs',
    )
    request_id = models.UUIDField(db_index=True)
    method = models.CharField(max_length=12)
    path = models.CharField(max_length=255)
    scope = models.CharField(max_length=80, blank=True)
    status_code = models.PositiveSmallIntegerField()
    remote_addr = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.method} {self.path} — {self.status_code}'
