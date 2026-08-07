import json
from io import StringIO

from django.core.management import call_command
from django.test import TestCase


class ExpenseReconciliationCommandTests(TestCase):
    def test_expense_scope_is_read_only_and_machine_readable(self):
        output = StringIO()
        call_command('reconcile_finance', scope='expenses', format='json', stdout=output)

        report = json.loads(output.getvalue())
        self.assertEqual(report['mode'], 'read-only')
        self.assertEqual(report['scope'], 'expenses')
        self.assertEqual(report['count'], len(report['findings']))

    def test_backfill_switch_is_not_available(self):
        with self.assertRaises(TypeError):
            call_command('reconcile_finance', apply_backfill=True)
