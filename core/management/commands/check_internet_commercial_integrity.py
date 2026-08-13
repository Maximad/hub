import json

from django.core.management.base import BaseCommand, CommandError

from core.internet_integrity import commercial_integrity_findings


class Command(BaseCommand):
    help = 'Read-only audit of membership allocations and Internet revenue snapshots.'

    def handle(self, *args, **options):
        findings = commercial_integrity_findings()
        self.stdout.write(json.dumps({'status': 'FAIL' if findings else 'PASS',
                                      'findings': findings}, ensure_ascii=False, indent=2))
        if findings:
            raise CommandError(f'{len(findings)} Internet commercial integrity finding(s).')
