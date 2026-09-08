import os
from unittest import mock

from django.test import TestCase

from audit.integration_auth import (
    IntegrationAuthError,
    authenticate_bearer_header,
    generate_token_value,
)
from audit.models import IntegrationToken


class SharedIntegrationBearerTests(TestCase):
    def setUp(self):
        self.env = mock.patch.dict(
            os.environ,
            {'HUB_MANAGEMENT_API_ENABLED': 'true'},
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def _token(self, *scopes):
        prefix, digest, complete = generate_token_value()
        record = IntegrationToken.objects.create(
            name='shared verifier test',
            prefix=prefix,
            secret_digest=digest,
            scopes=list(scopes),
        )
        return record, complete

    def test_header_auth_can_validate_identity_without_business_scope(self):
        record, complete = self._token('catalog.read')
        authenticated = authenticate_bearer_header(f'Bearer {complete}')
        self.assertEqual(authenticated.pk, record.pk)

    def test_header_auth_enforces_requested_scope(self):
        _record, complete = self._token('catalog.read')
        with self.assertRaises(IntegrationAuthError) as raised:
            authenticate_bearer_header(f'Bearer {complete}', 'inventory.read')
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.code, 'insufficient_scope')

    def test_rest_read_scope_does_not_grant_mcp_connection(self):
        _record, complete = self._token('catalog.read', 'inventory.read')
        with self.assertRaises(IntegrationAuthError) as raised:
            authenticate_bearer_header(f'Bearer {complete}', 'mcp.connect')
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.code, 'insufficient_scope')

    def test_explicit_mcp_connection_scope_is_accepted(self):
        record, complete = self._token('mcp.connect', 'catalog.read')
        authenticated = authenticate_bearer_header(f'Bearer {complete}', 'mcp.connect')
        self.assertEqual(authenticated.pk, record.pk)

    def test_header_auth_rejects_inactive_token(self):
        record, complete = self._token('catalog.read')
        record.is_active = False
        record.save(update_fields=['is_active'])
        with self.assertRaises(IntegrationAuthError):
            authenticate_bearer_header(f'Bearer {complete}', 'catalog.read')
