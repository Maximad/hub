"""Read-only audit of configured membership plans. No PII or commercial writes."""
import json

from django.core.management.base import BaseCommand, CommandError

from members.models import MembershipPlan
from members.plan_readiness import assess_membership_plan


class Command(BaseCommand):
    help = 'Audit membership plan configuration and benefits without changing records.'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', dest='json_output')
        parser.add_argument('--all', action='store_true', dest='include_inactive',
                            help='Include inactive/hidden plans and explain status.')
        parser.add_argument('--strict', action='store_true',
                            help='Fail when an offered plan has known blocking configuration.')

    def handle(self, *args, **options):
        plans = MembershipPlan.objects.prefetch_related(
            'benefit_rules__product', 'benefit_rules__category',
            'benefit_rules__internet_bandwidth_profile',
        ).order_by('sort_order', 'code')
        if not options['include_inactive']:
            plans = plans.filter(is_active=True, visible_to_staff=True)
        rows = []
        for plan in plans:
            finding = assess_membership_plan(plan)
            rows.append({
                'code': plan.code,
                'name_ar': plan.name_ar,
                'active': plan.is_active,
                'visible_to_staff': plan.visible_to_staff,
                'price_syp': plan.price_syp,
                'sellable_configuration': finding['can_sell'],
                'supported_benefits': finding['supported_benefits'],
                'configured_only': finding['configured_only'],
                'errors': finding['errors'],
                'warnings': finding['warnings'],
            })
        blocked = sum(bool(row['errors']) and row['active'] and row['visible_to_staff'] for row in rows)
        warnings = sum(bool(row['warnings']) for row in rows)
        report = {
            'counts': {'plans': len(rows), 'blocked': blocked, 'review': warnings},
            'scope': 'all_plans' if options['include_inactive'] else 'active_staff_offers',
            'source': 'current_connected_django_database',
            'plans': rows,
        }
        if options['json_output']:
            self.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True))
        else:
            self.stdout.write(
                f"Membership plans: {len(rows)} | blocked: {blocked} | review: {warnings}"
            )
            for row in rows:
                status = 'BLOCK' if row['errors'] else 'REVIEW' if row['warnings'] else 'OK'
                self.stdout.write(
                    f"[{status}] {row['code']}: {row['name_ar']} | "
                    f"{row['price_syp']} SYP | automatic={len(row['supported_benefits'])}"
                )
                for finding in row['errors']:
                    self.stdout.write(f'  ERROR: {finding}')
                for finding in row['warnings']:
                    self.stdout.write(f'  REVIEW: {finding}')
        if options['strict'] and blocked:
            raise CommandError('Membership readiness blocked: review plan errors above.')
