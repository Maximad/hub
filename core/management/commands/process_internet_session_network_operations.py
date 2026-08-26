import json

from django.core.management.base import BaseCommand, CommandError

from internet.session_network_operations import process_ready_session_network_operations


class Command(BaseCommand):
    help = 'Process a bounded batch of durable package-less Internet session network operations.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)
        parser.add_argument('--json', action='store_true')

    def handle(self, *args, **options):
        limit = options['limit']
        if limit < 1 or limit > 1000:
            raise CommandError('--limit must be between 1 and 1000')
        processed, succeeded = process_ready_session_network_operations(limit=limit)
        result = {
            'processed': processed,
            'succeeded': succeeded,
            'failed': processed - succeeded,
        }
        self.stdout.write(
            json.dumps(result)
            if options['json']
            else f"Processed {processed}: {succeeded} succeeded, {processed - succeeded} failed"
        )
        if processed != succeeded:
            raise CommandError(
                'One or more session network operations failed; retry remains durable.',
                returncode=1,
            )
