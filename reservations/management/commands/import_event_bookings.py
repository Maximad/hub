"""Private booking intake from local CSV; never prints attendee contact details."""
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from reservations.importer import BookingImportError, import_booking_csv


class Command(BaseCommand):
    help = (
        'Validate/preview CSV reservations with database rollback; --apply explicitly '
        'commits an atomic, idempotent import. Never prints guest contacts.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--file', required=True, help='Absolute path to private CSV on server')
        parser.add_argument('--source', required=True, help='Stable non-personal source slug')
        parser.add_argument('--actor', required=True, help='Authorized admin username')
        parser.add_argument('--apply', action='store_true', help='Commit changes only after preview')
        parser.add_argument('--expected-rows', type=int, help='Required on --apply to confirm file size')

    def handle(self, *args, **opts):
        try:
            path = Path(opts['file']).expanduser()
            if not path.is_file() or path.stat().st_size > 1_000_000:
                raise CommandError('CSV missing or larger than 1 MB.')
            text = path.read_text(encoding='utf-8-sig')
        except (OSError, UnicodeError):
            raise CommandError('Could not read UTF-8 CSV.')
        user = get_user_model().objects.filter(username=opts['actor'], is_active=True).first()
        if not user:
            raise CommandError('Active manager account not found.')
        if opts['apply'] and (opts['expected_rows'] is None or opts['expected_rows'] < 0):
            raise CommandError('--apply requires --expected-rows matching the validated CSV.')
        try:
            result = import_booking_csv(
                text, source=opts['source'], actor=user, apply=opts['apply'],
            )
        except BookingImportError as error:
            raise CommandError(f'Import rejected at CSV row {error.row}: {error.code}')
        except Exception as error:
            from django.core.exceptions import ValidationError
            if isinstance(error, ValidationError):
                raise CommandError('Import rejected: insufficient authority or invalid configuration.')
            raise
        if opts['apply'] and result['rows'] != opts['expected_rows']:
            # Important: expected-rows must be validated BEFORE apply. Caller
            # must never reach this condition with already-committed records.
            raise CommandError('Import row count does not match --expected-rows.')
        self.stdout.write(
            f"{'APPLIED' if result['applied'] else 'PREVIEW / ROLLED BACK'} "
            f"rows={result['rows']} created={result['created']} "
            f"skipped={result['skipped']} unclassified={result['unclassified']}"
        )
