import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from core.internet_integrity import access_integrity_findings, commercial_integrity_findings
from core.models import (InternetBandwidthProfile, InternetNetworkOperation,
                         InternetPackage, InternetPartner)


class Command(BaseCommand):
    help = 'Read-only paid Internet configuration and integrity preflight.'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', dest='as_json')
        parser.add_argument('--strict', action='store_true')

    def handle(self, *args, **options):
        findings = []
        def add(severity, code, message, **details):
            findings.append({'severity': severity, 'code': code, 'message': message, **details})

        packages = InternetPackage.objects.filter(is_active=True).select_related('partner', 'bandwidth_profile')
        if not packages.exists():
            add('WARN', 'no_active_packages', 'No active Internet packages are configured.')
        for package in packages:
            try:
                package.clean()
            except ValidationError as exc:
                add('FAIL', 'invalid_package', '; '.join(exc.messages), package_id=package.pk)
        if not InternetPartner.objects.filter(active=True, is_default=True).exists():
            add('WARN', 'no_default_partner', 'No default partner; partnerless sales create no liability.')
        if InternetBandwidthProfile.objects.filter(is_active=False, packages__is_active=True).exists():
            add('FAIL', 'inactive_package_profile', 'An active package uses an inactive bandwidth profile.')
        failed = InternetNetworkOperation.objects.filter(status=InternetNetworkOperation.Status.FAILED).count()
        pending = InternetNetworkOperation.objects.filter(status__in=(
            InternetNetworkOperation.Status.PENDING, InternetNetworkOperation.Status.PROCESSING)).count()
        if failed:
            add('WARN', 'failed_network_operations', 'Failed network operations require review.', count=failed)
        if pending:
            add('WARN', 'pending_network_operations', 'Network operations remain pending.', count=pending)
        for item in commercial_integrity_findings() + access_integrity_findings():
            add('FAIL', item.pop('code'), 'Internet business invariant violation.', **item)
        if settings.MIKROTIK_ENABLED:
            if not settings.MIKROTIK_BASE_URL or not settings.MIKROTIK_HOTSPOT_SERVER:
                add('FAIL', 'mikrotik_incomplete', 'MikroTik is enabled but required configuration is incomplete.')
        else:
            add('WARN', 'mikrotik_disabled', 'MikroTik disabled; Manual network enforcement is active.')

        status = 'FAIL' if any(f['severity'] == 'FAIL' for f in findings) else ('WARN' if findings else 'PASS')
        payload = {'status': status, 'findings': findings}
        if options['as_json']:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            self.stdout.write(status)
            for finding in findings:
                self.stdout.write(f"{finding['severity']}: {finding['message']} [{finding['code']}]")
        if status == 'FAIL' or (options['strict'] and status == 'WARN'):
            raise CommandError('Internet readiness failed.')
