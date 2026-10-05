from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import ActivityLog, Product
from core.services.menu_tools import apply_product_bulk_action


FACTOR = 100
MARKER_ACTION = 'legacy_currency_price_normalized'
# Monthly internet subscriptions are already expressed in the new lira and must not be divided.
MONTHLY_INTERNET_PUBLIC_CODES = {
    'c472c751-43cd-4a30-8f49-164383260905',  # شهر — ١٠٠ ساعة
    '8825de52-85d4-43e0-8f15-c67e7dcd4e95',  # شهر مكثف — ١٨٠ ساعة
}


class Command(BaseCommand):
    help = (
        'Preview or apply the one-time old-lira -> new-lira normalization for product prices. '
        'Products above 1000 are divided by 100, except confirmed monthly internet subscriptions.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Persist the reviewed changes. Default is preview only.')
        parser.add_argument('--actor', help='Optional username to attribute ActivityLog entries to.')

    def handle(self, *args, **options):
        actor = self._actor(options.get('actor'))
        candidates = list(Product.objects.filter(price_syp__gt=1000).order_by('name_ar', 'pk'))
        rows = []
        skipped_monthly = []
        skipped_nondivisible = []
        already_done = []

        for product in candidates:
            public_code = str(product.public_code)
            if public_code in MONTHLY_INTERNET_PUBLIC_CODES:
                skipped_monthly.append(product)
                continue
            if ActivityLog.objects.filter(
                action=MARKER_ACTION,
                details__product_public_code=public_code,
            ).exists():
                already_done.append(product)
                continue
            if product.price_syp % FACTOR:
                skipped_nondivisible.append(product)
                continue
            rows.append((product, product.price_syp, product.price_syp // FACTOR))

        self.stdout.write('Legacy currency price normalization preview')
        for product, before, after in rows:
            self.stdout.write(f'  {product.name_ar} [{product.public_code}]: {before} -> {after}')
        for product in skipped_monthly:
            self.stdout.write(self.style.WARNING(
                f'  KEEP monthly internet: {product.name_ar} [{product.public_code}] = {product.price_syp}'
            ))
        for product in skipped_nondivisible:
            self.stdout.write(self.style.WARNING(
                f'  SKIP non-divisible price (manual review): {product.name_ar} [{product.public_code}] = {product.price_syp}'
            ))
        for product in already_done:
            self.stdout.write(f'  ALREADY NORMALIZED: {product.name_ar} [{product.public_code}]')

        self.stdout.write(
            f'Candidates={len(rows)} | monthly_kept={len(skipped_monthly)} | '
            f'nondivisible_skipped={len(skipped_nondivisible)} | already_done={len(already_done)}'
        )

        if not options['apply']:
            self.stdout.write(self.style.WARNING('Preview only. Re-run with --apply after review.'))
            return

        changed = 0
        for product, before, after in rows:
            with transaction.atomic():
                locked = Product.objects.select_for_update().get(pk=product.pk)
                if locked.price_syp != before:
                    raise CommandError(
                        f'Price changed during review for {locked.name_ar}: expected {before}, found {locked.price_syp}. '
                        'Run preview again.'
                    )
                if ActivityLog.objects.filter(
                    action=MARKER_ACTION,
                    details__product_public_code=str(locked.public_code),
                ).exists():
                    continue
                apply_product_bulk_action(
                    [locked.pk],
                    'set_exact_price',
                    str(after),
                    actor=actor,
                    base_queryset=Product.objects.all(),
                )
                ActivityLog.objects.create(
                    actor=actor,
                    action=MARKER_ACTION,
                    details={
                        'product_id': locked.pk,
                        'product_public_code': str(locked.public_code),
                        'product_name_ar': locked.name_ar,
                        'before_syp': before,
                        'after_syp': after,
                        'factor': FACTOR,
                        'reason': 'old_lira_to_new_lira_remove_two_zeros',
                    },
                )
                changed += 1

        self.stdout.write(self.style.SUCCESS(f'Applied {changed} product price normalizations.'))

    def _actor(self, username):
        if not username:
            return None
        User = get_user_model()
        try:
            return User.objects.get(username=username, is_active=True)
        except User.DoesNotExist as exc:
            raise CommandError(f'Active user not found: {username}') from exc
