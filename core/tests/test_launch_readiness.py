import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase


class LaunchReadinessCommandContractTests(SimpleTestCase):
    """Cover orchestration, output, and exit behavior for every audit gate."""

    GATES = (
        '_migrations', '_security', '_storage', '_backup', '_reference_data',
        '_admin', '_usd', '_rollout', '_integrity', '_routes', '_network',
    )

    def run_with_status(self, status='PASS', **options):
        from core.management.commands.launch_readiness import Command

        stack = []
        for gate in self.GATES:
            mocked = patch.object(
                Command, gate, autospec=True,
                side_effect=lambda command, g=gate: command.add(
                    status, g[1:], 'نتيجة آمنة', 'المعالجة المطلوبة'),
            )
            mocked.start(); stack.append(mocked)
        operational = patch.object(
            Command, '_operational', autospec=True,
            side_effect=lambda command, allowed: command.add(
                status, 'prelaunch_operational_data', 'نتيجة آمنة', 'المعالجة المطلوبة'),
        )
        operational.start(); stack.append(operational)
        self.addCleanup(lambda: [item.stop() for item in reversed(stack)])
        output = StringIO()
        call_command('launch_readiness', stdout=output, **options)
        return output.getvalue()

    def test_all_required_gates_are_run_and_json_is_machine_readable(self):
        payload = json.loads(self.run_with_status(as_json=True))
        self.assertTrue(payload['read_only'])
        self.assertEqual(payload['status'], 'PASS')
        self.assertEqual(
            {row['code'] for row in payload['checks']},
            {gate[1:] for gate in self.GATES} | {'prelaunch_operational_data'},
        )

    def test_warn_succeeds_normally(self):
        self.assertIn('النتيجة: WARN', self.run_with_status(status='WARN'))

    def test_warn_fails_in_strict_mode(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_with_status(status='WARN', strict=True)
        self.assertEqual(raised.exception.code, 1)

    def test_fail_returns_nonzero(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_with_status(status='FAIL')
        self.assertEqual(raised.exception.code, 1)

    def test_json_never_exposes_sensitive_values_and_documents_resolution(self):
        payload = json.loads(self.run_with_status(as_json=True))
        rendered = json.dumps(payload)
        for forbidden in ('SECRET_KEY=', 'password=', 'connection_string=', 'cookie='):
            self.assertNotIn(forbidden, rendered)
        self.assertTrue(all(row['resolution'] for row in payload['checks']))
