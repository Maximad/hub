from __future__ import annotations

import calendar
import csv
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import ActivityLog, Order, OrderItem, Product

DEFAULT_FILE = Path(settings.BASE_DIR) / 'data' / 'pre_system_sales_2026_07_09.csv'
TZ = ZoneInfo('Asia/Damascus')
NAMESPACE = uuid.UUID('f5831e26-07d4-4ad5-9e6f-e34002c14556')
REVIEW_STATUSES = {'confirmed', 'needs_review'}


class Command(BaseCommand):
    help = 'Import aggregated pre-system sales into existing Order/OrderItem models without payments or stock deductions.'

    def add_arguments(self, parser):
        parser.add_argument('--file', default=str(DEFAULT_FILE))
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--actor')

    def handle(self, *args, **options):
        path = Path(options['file'])
        if not path.exists():
            raise CommandError(f'File not found: {path}')
        actor = self._actor(options.get('actor'))
        rows = self._read(path)
        totals = defaultdict(lambda: {'units': 0, 'gross': 0, 'review': 0})
        for row in rows:
            data = totals[row['month']]
            data['units'] += row['quantity']
            data['gross'] += row['quantity'] * row['price']
            data['review'] += row['review_status'] == 'needs_review'
            self.stdout.write(
                f"{row['month']} | {row['raw_name']} -> {row['product'].name_ar} | "
                f"{row['quantity']} x {row['price']} | {row['review_status']}"
            )
        for month in sorted(totals):
            d = totals[month]
            self.stdout.write(f"{month}: units={d['units']} gross_snapshot={d['gross']} review_rows={d['review']}")
        if not options['apply']:
            self.stdout.write(self.style.WARNING('Preview only; no database changes.'))
            return

        created_orders = updated_orders = created_items = updated_items = 0
        with transaction.atomic():
            orders = {}
            for month in sorted({r['month'] for r in rows}):
                order, created = self._order(month)
                orders[month] = order
                created_orders += int(created)
                updated_orders += int(not created)
            for row in rows:
                order = orders[row['month']]
                key = f"{row['month']}:{row['raw_name']}"
                marker = f'import_key={key}'
                note = self._note(row, key)
                existing = OrderItem.objects.filter(order=order, item_note__contains=marker).order_by('pk').first()
                values = dict(
                    product_id=row['product'].pk,
                    quantity=row['quantity'],
                    product_name_ar_snapshot=row['raw_name'],
                    product_name_en_snapshot='',
                    unit_price_syp_snapshot=row['price'],
                    selected_options_snapshot=[],
                    item_note=note,
                    line_total_syp_snapshot=row['quantity'] * row['price'],
                    estimated_unit_cost_syp_snapshot=None,
                    estimated_line_cost_syp_snapshot=None,
                    estimated_line_margin_syp_snapshot=None,
                    prep_station_id=None,
                    prep_status=OrderItem.PrepStatus.NO_PREP,
                    stock_deducted=False,
                    stock_deducted_at=None,
                    stock_deduction_error='',
                )
                if existing:
                    OrderItem.objects.filter(pk=existing.pk).update(**values)
                    updated_items += 1
                else:
                    item = OrderItem.objects.create(
                        order=order,
                        product=row['product'],
                        quantity=row['quantity'],
                        product_name_ar_snapshot=row['raw_name'],
                        unit_price_syp_snapshot=row['price'],
                        item_note=note,
                        line_total_syp_snapshot=row['quantity'] * row['price'],
                    )
                    OrderItem.objects.filter(pk=item.pk).update(**values)
                    created_items += 1
            ActivityLog.objects.create(
                actor=actor,
                action='pre_system_historical_sales_import',
                details={
                    'source_file': path.name,
                    'months': sorted(totals),
                    'units_by_month': {m: totals[m]['units'] for m in sorted(totals)},
                    'gross_snapshot_by_month': {m: totals[m]['gross'] for m in sorted(totals)},
                    'rows_needing_review': sum(d['review'] for d in totals.values()),
                    'payments_created': 0,
                    'stock_movements_created': 0,
                },
            )
        self.stdout.write(self.style.SUCCESS(
            f'Imported orders +{created_orders}/~{updated_orders}; items +{created_items}/~{updated_items}. '
            'No payments or stock deductions created.'
        ))

    def _read(self, path):
        required = {'month', 'product_public_code', 'raw_name', 'quantity', 'unit_price_syp', 'review_status', 'notes'}
        result = []
        with path.open('r', encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle)
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise CommandError('Missing CSV columns: ' + ', '.join(sorted(missing)))
            for line, raw in enumerate(reader, 2):
                month = (raw['month'] or '').strip()
                try:
                    year, number = [int(x) for x in month.split('-', 1)]
                    if not 1 <= number <= 12:
                        raise ValueError
                    datetime(year, number, 1)
                except ValueError as exc:
                    raise CommandError(f'Line {line}: invalid month; use YYYY-MM.') from exc
                try:
                    code = uuid.UUID((raw['product_public_code'] or '').strip())
                except ValueError as exc:
                    raise CommandError(f'Line {line}: invalid product_public_code.') from exc
                product = Product.objects.filter(public_code=code).first()
                if not product:
                    raise CommandError(f'Line {line}: product not found: {code}')
                raw_name = (raw['raw_name'] or '').strip()
                if not raw_name:
                    raise CommandError(f'Line {line}: raw_name is required.')
                try:
                    quantity = int(raw['quantity'])
                    price = int(raw['unit_price_syp'])
                except ValueError as exc:
                    raise CommandError(f'Line {line}: quantity and unit_price_syp must be integers.') from exc
                if quantity <= 0 or price < 0:
                    raise CommandError(f'Line {line}: quantity must be > 0 and price >= 0.')
                review = (raw['review_status'] or 'confirmed').strip().lower()
                if review not in REVIEW_STATUSES:
                    raise CommandError(f'Line {line}: invalid review_status.')
                result.append(dict(
                    month=month,
                    product=product,
                    raw_name=raw_name,
                    quantity=quantity,
                    price=price,
                    review_status=review,
                    notes=(raw['notes'] or '').strip(),
                ))
        return result

    def _order(self, month):
        year, number = [int(x) for x in month.split('-', 1)]
        day = calendar.monthrange(year, number)[1]
        historical_ts = timezone.make_aware(datetime(year, number, day, 12, 0), TZ)
        public_code = uuid.uuid5(NAMESPACE, f'hub-pre-system-sales:{month}')
        notes = (
            f'[PRE_SYSTEM_HISTORICAL_SALES] month={month}\n'
            'إدخال تاريخي مجمع قبل اعتماد Hub Suite. الكميات والأسعار من سجل المبيعات اليدوي.\n'
            'الحسومات والضيافة وطرق الدفع غير مكتملة وتحتاج تدقيقاً. لا توجد حركة مخزون ناتجة عن هذا الاستيراد.'
        )
        order, created = Order.objects.get_or_create(
            public_code=public_code,
            defaults=dict(
                service_mode=Order.ServiceMode.DINE_IN,
                fulfillment_mode=Order.FulfillmentMode.INSIDE_SPACE,
                status=Order.Status.SERVED,
                notes=notes,
            ),
        )
        if not created:
            Order.objects.filter(pk=order.pk).update(
                service_mode=Order.ServiceMode.DINE_IN,
                fulfillment_mode=Order.FulfillmentMode.INSIDE_SPACE,
                status=Order.Status.SERVED,
                notes=notes,
            )
        Order.objects.filter(pk=order.pk).update(created_at=historical_ts)
        order.refresh_from_db()
        return order, created

    def _note(self, row, key):
        text = f"[PRE_SYSTEM_HISTORICAL_SALE_ITEM] | import_key={key} | review={row['review_status']}"
        if row['notes']:
            text += ' | ' + row['notes']
        return text

    def _actor(self, username):
        if not username:
            return None
        User = get_user_model()
        try:
            return User.objects.get(username=username, is_active=True)
        except User.DoesNotExist as exc:
            raise CommandError(f'Active user not found: {username}') from exc
