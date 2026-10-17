"""Stage the unapproved exchange-rate JSON from public GitHub or an explicit local file."""
import json
from pathlib import Path
from urllib.request import Request, urlopen

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from core.services.exchange_rate_feed import PUBLIC_RATE_URL, parse_unapproved_record, stage_unapproved_rate

MAX_SIZE = 32768


class Command(BaseCommand):
    help = 'Validate and stage external USD rates; never approves or posts a financial entry.'

    def add_arguments(self, parser):
        parser.add_argument('--file', type=Path, help='Read a local file instead of the fixed public GitHub URL.')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        try:
            if options['file']:
                raw = options['file'].read_bytes()
            else:
                request = Request(PUBLIC_RATE_URL, headers={
                    'Accept': 'application/json', 'User-Agent': 'HubSweidaExchangeRateImport/1',
                    'Cache-Control': 'no-cache',
                })
                with urlopen(request, timeout=15) as response:
                    if 'application/json' not in response.headers.get('Content-Type', '').lower():
                        raise CommandError('Public rate feed did not return JSON.')
                    raw = response.read(MAX_SIZE + 1)
            if len(raw) > MAX_SIZE:
                raise CommandError('Rate feed is unexpectedly large.')
            value = json.loads(raw)
            if options['dry_run']:
                parsed = parse_unapproved_record(value)
                self.stdout.write(f'VALID (no changes): {parsed["business_date"]}')
                return
            row, created = stage_unapproved_rate(value)
        except (OSError, ValueError, ValidationError, KeyError) as exc:
            raise CommandError(f'Cannot stage exchange rate: {exc}') from exc
        self.stdout.write(
            f'{"STAGED" if created else "ALREADY-STAGED"} rate candidate #{row.pk} '
            f'(awaiting superuser approval; no accounting writes).'
        )
