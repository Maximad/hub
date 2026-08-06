from django.core.management.base import BaseCommand, CommandError
from core.services.posting.reconciliation import record_unsupported_bypasses


class Command(BaseCommand):
    help = 'Record and fail on every financial record written outside the posting service.'

    def handle(self, *args, **options):
        failures = record_unsupported_bypasses()
        if failures:
            raise CommandError(f'{len(failures)} unsupported posting bypass(es) recorded.')
        self.stdout.write(self.style.SUCCESS('No unsupported posting bypasses.'))
