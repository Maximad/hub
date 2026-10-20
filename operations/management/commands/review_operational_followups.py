"""Preview or explicitly sync bounded source-backed review cases.

No cases are created from imported aggregate notebook sales. Default preview
rolls back database writes, including daily task projections and audit.
"""
import json

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from operations.models import BusinessDay
from operations.review_cases import run_review_sync


class Command(BaseCommand):
    help = 'Preview current operations review cases; --apply --actor required for writes.'

    def add_arguments(self, parser):
        parser.add_argument('--product-id', type=int, action='append', default=[])
        parser.add_argument('--inventory-id', type=int, action='append', default=[])
        parser.add_argument('--internet-session-id', type=int, action='append', default=[])
        parser.add_argument('--purchase-id', type=int, action='append', default=[])
        parser.add_argument('--limit', type=int, default=20)
        parser.add_argument('--no-recent', action='store_true')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--actor')
        parser.add_argument('--json', action='store_true', dest='as_json')

    def handle(self, *args, **options):
        day = BusinessDay.objects.filter(
            status__in=[BusinessDay.Status.OPEN, BusinessDay.Status.CLOSING, BusinessDay.Status.REOPENED]
        ).order_by('-business_date').first()
        if day is None:
            raise CommandError('No open Business Day; this command never opens one automatically.')
        actor = None
        if options['apply']:
            if not options.get('actor'):
                raise CommandError('--apply requires an identified --actor staff username.')
            actor = get_user_model().objects.filter(
                username=options['actor'], is_active=True, is_staff=True,
            ).first()
            if actor is None or not (actor.is_superuser or actor.role in {'admin', 'cashier'}):
                raise CommandError('Actor must be a current active managerial staff user.')
        try:
            result = run_review_sync(
                day, actor=actor, product_ids=options['product_id'],
                inventory_ids=options['inventory_id'],
                internet_ids=options['internet_session_id'],
                purchase_ids=options['purchase_id'],
                include_recent=not options['no_recent'], limit=options['limit'],
                dry_run=not options['apply'],
            )
        except (ValidationError, ValueError, TypeError) as exc:
            raise CommandError(' '.join(getattr(exc, 'messages', [str(exc)]))) from exc
        result['business_date'] = str(day.business_date)
        if options['as_json']:
            self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            self.stdout.write('APPLIED' if options['apply'] else 'PREVIEW ONLY: rolled back')
            for change in result['changes']:
                self.stdout.write(change)
            self.stdout.write('Counts: ' + json.dumps(result['counts'], ensure_ascii=False))
            self.stdout.write('No orders, inventory quantities, financial postings or notifications modified.')
