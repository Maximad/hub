import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import InternetEntitlement, InternetNetworkOperation, InternetSession
from core.services.internet_lifecycle import (expire_internet_entitlement,
                                               expire_membership,
                                               unfreeze_membership)
from core.services.internet_access import end_usage_session
from core.services.network_operations import enqueue_network_operation
from members.models import MembershipSubscription


class Command(BaseCommand):
    help = 'Reconcile membership, entitlement, session, and durable network lifecycle.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=200)
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--json', action='store_true')

    def handle(self, *args, **options):
        limit = options['limit']
        if not 1 <= limit <= 1000:
            raise CommandError('--limit must be between 1 and 1000')
        now = timezone.now()
        expiring = list(MembershipSubscription.objects.filter(
            status=MembershipSubscription.Status.ACTIVE, ends_at__lte=now
        ).order_by('ends_at').values_list('pk', flat=True)[:limit])
        thawing = list(MembershipSubscription.objects.filter(
            status=MembershipSubscription.Status.FROZEN, freeze_until__lte=now
        ).order_by('freeze_until').values_list('pk', flat=True)[:limit])
        entitlement_ids = list(InternetEntitlement.objects.filter(
            status__in=(InternetEntitlement.Status.ACTIVE, InternetEntitlement.Status.PENDING),
            valid_until__lte=now).order_by('valid_until').values_list('pk', flat=True)[:limit])
        overdue = list(InternetSession.objects.filter(
            status=InternetSession.Status.ACTIVE, authorized_until__lte=now
        ).order_by('authorized_until').values_list('pk', flat=True)[:limit])
        result = {'subscriptions_to_expire': expiring, 'freezes_to_thaw': thawing,
                  'entitlements_to_expire': entitlement_ids,
                  'overdue_sessions_to_close': overdue,
                  'network_operations_to_enqueue': len(set(entitlement_ids)) + len(overdue)}
        if not options['dry_run']:
            for pk in thawing:
                unfreeze_membership(MembershipSubscription(pk=pk), at=now)
            for pk in expiring:
                expire_membership(MembershipSubscription(pk=pk), at=now)
            for pk in entitlement_ids:
                expire_internet_entitlement(InternetEntitlement(pk=pk), effective_at=None)
            for pk in overdue:
                session = InternetSession.objects.select_related('entitlement').filter(
                    pk=pk, status=InternetSession.Status.ACTIVE).first()
                if not session:
                    continue
                ended = end_usage_session(session, at=session.authorized_until)
                ended.lifecycle_end_reason = 'authorization_expired'
                ended.save(update_fields=('lifecycle_end_reason', 'updated_at'))
                enqueue_network_operation(
                    session.entitlement, InternetNetworkOperation.Operation.REFRESH,
                    reason='authorization_expired',
                    idempotency_key=f'internet-session:{session.pk}:authorization-expired')
        output = json.dumps(result) if options['json'] else '\n'.join(
            f'{key}: {value}' for key, value in result.items())
        self.stdout.write(output)
