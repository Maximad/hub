import json

from django.core.management.base import BaseCommand, CommandError

from core.services.internet_readiness import internet_readiness_report


class Command(BaseCommand):
    help = 'Read-only paid Internet configuration and integrity preflight.'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', dest='as_json')
        parser.add_argument('--strict', action='store_true')

    def handle(self, *args, **options):
        payload = internet_readiness_report()
        status = payload['status']
        if options['as_json']:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            self.stdout.write(status)
            for finding in payload['findings']:
                self.stdout.write(
                    f"{finding['severity']}: {finding['message']} [{finding['code']}]"
                )
        if status == 'FAIL' or (options['strict'] and status == 'WARN'):
            raise CommandError('Internet readiness failed.')
