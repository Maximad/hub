import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import SimpleTestCase

from core.management.commands.import_hub_operations_batch import HEADERS, ORDER, SHEETS
from core.management.commands.import_inventory_items import _text, _xlsx_rows


class OperationsTemplateTests(SimpleTestCase):
    def test_template_has_exact_importable_headers_and_examples(self):
        with tempfile.TemporaryDirectory() as directory:
            workbook = Path(directory) / 'operations.xlsx'
            call_command('generate_hub_operations_template', workbook)

            for section in ORDER:
                rows = list(_xlsx_rows(workbook, SHEETS[section]))
                self.assertEqual([_text(value) for value in rows[0]], HEADERS[section])
                self.assertGreaterEqual(len(rows), 2)

    def test_template_refuses_to_overwrite_a_file(self):
        with tempfile.TemporaryDirectory() as directory:
            workbook = Path(directory) / 'operations.xlsx'
            workbook.write_bytes(b'existing')
            with self.assertRaisesMessage(Exception, 'Refusing to overwrite'):
                call_command('generate_hub_operations_template', workbook)
