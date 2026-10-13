"""Read-only, privacy-safe consolidated Internet release verification.

No RouterOS calls, database writes, account identifiers or PII. This command
must be safe to run before the first cutover while prior customer records exist.
"""
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import InternetEntitlement, InternetNetworkOperation, InternetSession
from core.services.internet_internal_access import INTERNAL_ORIGINS
from internet.models import (
    InternalStaffInternetGrant, StaffInternetConnectionSnapshot,
    StaffInternetDeviceEvidence, StaffInternetObservedInterval,
)


class Command(BaseCommand):
    help = 'Read-only pre/post Internet release summary, without customer or router secrets.'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', dest='as_json')

    def handle(self, *args, **options):
        findings = []

        def finding(code, status, detail):
            findings.append({'code': code, 'status': status, 'detail': detail})

        grants = InternalStaffInternetGrant.objects.select_related('entitlement', 'user')
        live = (InternetEntitlement.Status.PENDING, InternetEntitlement.Status.ACTIVE,
                InternetEntitlement.Status.SUSPENDED)
        active_grants = grants.filter(entitlement__status__in=live)
        inactive_staff = active_grants.filter(user__is_active=False).count()
        if inactive_staff:
            finding('inactive_staff_grants', 'WARN',
                    'Inactive staff retain grants pending normal worker revocation.')
        failed_ops = InternetNetworkOperation.objects.filter(
            status=InternetNetworkOperation.Status.FAILED,
            entitlement__origin_type__in=INTERNAL_ORIGINS,
        ).count()
        if failed_ops:
            finding('staff_network_failures', 'WARN',
                    'Failed staff network operations need operator inspection.')
        pending_ops = InternetNetworkOperation.objects.filter(
            status__in=(InternetNetworkOperation.Status.PENDING,
                        InternetNetworkOperation.Status.PROCESSING),
            entitlement__origin_type__in=INTERNAL_ORIGINS,
        ).count()
        if pending_ops:
            finding('staff_network_pending', 'WARN',
                    'Staff network operations are awaiting worker processing.')

        unidentified_active = InternetSession.objects.filter(
            status=InternetSession.Status.ACTIVE,
            billing_mode=InternetSession.BillingMode.OPEN_METERED,
            visit__isnull=False, visit__table__isnull=True,
            visit__guest_name='', visit__guest_phone='',
        ).count()
        if unidentified_active:
            finding('legacy_unidentified_sessions', 'WARN',
                    'Legacy active standalone sessions need manual review; do not rewrite.')

        if settings.MIKROTIK_ENABLED:
            if settings.MIKROTIK_VERIFY_TLS is not True:
                finding('mikrotik_tls', 'FAIL', 'RouterOS TLS verification must remain enabled.')
            ca = getattr(settings, 'MIKROTIK_CA_FILE', '')
            if not ca or not Path(ca).is_file():
                finding('mikrotik_ca', 'FAIL',
                        'Configured RouterOS CA is missing or unreadable inside Django.')
        status = ('FAIL' if any(x['status'] == 'FAIL' for x in findings)
                  else 'WARN' if findings else 'PASS')
        summary = {
            'staff_grants_total': grants.count(),
            'staff_grants_usable_status': active_grants.filter(user__is_active=True).count(),
            'staff_grants_inactive_staff': inactive_staff,
            'staff_network_operations_failed': failed_ops,
            'staff_network_operations_pending': pending_ops,
            'legacy_active_unidentified_metered_sessions': unidentified_active,
            'staff_connection_snapshots': StaffInternetConnectionSnapshot.objects.count(),
            'observed_network_address_records': StaffInternetDeviceEvidence.objects.count(),
            'sampled_continuity_intervals': StaffInternetObservedInterval.objects.count(),
            'quota_enforcement_from_observations': False,
        }
        payload = {'status': status, 'summary': summary, 'findings': findings}
        if options['as_json']:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            self.stdout.write(f'INTERNET RELEASE AUDIT: {status}')
            for item in findings:
                self.stdout.write(f"{item['status']} [{item['code']}] {item['detail']}")
        if status == 'FAIL':
            raise CommandError('Internet release preflight has blocking failures.')
        return None
