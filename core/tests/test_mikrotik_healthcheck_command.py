import io
import json
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings


@override_settings(MIKROTIK_ENABLED=True)
class MikroTikHealthcheckCommandTests(SimpleTestCase):
    @patch(
        'core.management.commands.mikrotik_healthcheck.run_readonly_mikrotik_healthcheck',
        return_value=(True, 'اتصال MikroTik للقراءة فقط ناجح.'),
    )
    def test_healthcheck_uses_persisting_operations_service(self, healthcheck):
        out = io.StringIO()

        call_command('mikrotik_healthcheck', '--json', stdout=out)

        payload = json.loads(out.getvalue())
        self.assertEqual(payload['ok'], True)
        self.assertEqual(payload['enabled'], True)
        self.assertEqual(payload['message'], 'اتصال MikroTik للقراءة فقط ناجح.')
        healthcheck.assert_called_once_with()
