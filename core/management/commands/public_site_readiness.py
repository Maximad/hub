"""Read-only public discovery and attendance readiness command."""
import json

from django.core.management.base import BaseCommand, CommandError

from core.services.public_site_readiness import public_site_readiness_report


class Command(BaseCommand):
    help = 'Audit public discovery, feature fallback and attendance configuration without changing records.'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', dest='as_json')
        parser.add_argument('--strict', action='store_true', help='Exit nonzero on any warning.')
        parser.add_argument('--limit', type=int, default=100,
                            help='Maximum records inspected per sampled section (1–500; default 100).')

    def handle(self, *args, **options):
        if not 1 <= options['limit'] <= 500:
            raise CommandError('--limit must be between 1 and 500.')
        report = public_site_readiness_report(limit=options['limit'])
        if options['as_json']:
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            self.stdout.write('Public site readiness (read-only; current command environment)')
            self.stdout.write(f"Checked: {report['checked_at']} ({report['timezone']})")
            for check in report['checks']:
                self.stdout.write(f"{check['status']:4} {check['code']}: {check['message']}")
                if check['status'] != 'PASS':
                    self.stdout.write(f"     Review: {check['resolution']}")
            for limitation in report['limitations']:
                self.stdout.write(f'Limit: {limitation}')
            self.stdout.write(f"Result: {report['status']} — {report['counts']}")
        if report['counts']['FAIL'] or (options['strict'] and report['counts']['WARN']):
            raise CommandError('Public site readiness requires review; see the report above.')
