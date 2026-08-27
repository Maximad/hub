from unittest.mock import Mock

from django.test import SimpleTestCase

from core.services.mikrotik import RouterOSClient


class RouterOSResourceIdPathTests(SimpleTestCase):
    def client(self):
        client = object.__new__(RouterOSClient)
        client._call = Mock(return_value={})
        return client

    def test_update_hotspot_user_preserves_routeros_id_marker(self):
        client = self.client()

        client.update_hotspot_user('*3', {'disabled': 'true'})

        client._call.assert_called_once_with(
            'PATCH',
            'ip/hotspot/user/*3',
            {'disabled': 'true'},
        )

    def test_remove_active_preserves_routeros_id_marker(self):
        client = self.client()

        client.remove_active('*A')

        client._call.assert_called_once_with(
            'DELETE',
            'ip/hotspot/active/*A',
        )
