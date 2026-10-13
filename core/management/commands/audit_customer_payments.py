"""Read-only, operator-scoped order / visit / collection consistency diagnostic.

This is not a reconciliation writer or a production release gate. Never change
the Payment, CashMovement, PostingBatch or InternetSession state from this tool.
"""
import json
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from core.models import (
    CashMovement, HubVisit, InternetSession, Order, Payment,
    PostingBatch, PostingEntry,
)
from core.services.visit_settlement import visit_financials


class Command(BaseCommand):
    help = 'Inspect a customer payment or visit without mutating financial records.'

    def add_arguments(self, parser):
        scope = parser.add_mutually_exclusive_group(required=True)
        scope.add_argument('--payment-id', type=int)
        scope.add_argument('--order-id', type=int)
        scope.add_argument('--visit-code')
        scope.add_argument('--recent', type=int, metavar='N')
        parser.add_argument('--json', action='store_true', dest='as_json')

    def handle(self, *args, **options):
        recent = options['recent']
        if recent is not None and not 1 <= recent <= 30:
            raise CommandError('--recent must be between 1 and 30.')
        if options['payment_id'] is not None and options['payment_id'] < 1:
            raise CommandError('--payment-id must be positive.')
        if options['order_id'] is not None and options['order_id'] < 1:
            raise CommandError('--order-id must be positive.')

        if options['payment_id'] is not None:
            payment = Payment.objects.select_related('order', 'order__visit').filter(
                pk=options['payment_id'],
            ).first()
            if payment is None:
                raise CommandError('Payment not found.')
            orders = [payment.order]
        elif options['order_id'] is not None:
            order = Order.objects.select_related('visit').filter(pk=options['order_id']).first()
            if order is None:
                raise CommandError('Order not found.')
            orders = [order]
        elif options['visit_code'] is not None:
            visit = HubVisit.objects.filter(public_code=options['visit_code']).first()
            if visit is None:
                raise CommandError('Visit not found.')
            orders = list(Order.objects.filter(visit=visit).order_by('pk'))
        else:
            latest = list(
                Payment.objects.filter(method__in=(
                    Payment.Method.CASH, Payment.Method.MANUAL_TRANSFER,
                )).select_related('order', 'order__visit').order_by('-created_at', '-pk')[:recent]
            )
            orders = [payment.order for payment in latest]

        # No ContentType.get_for_model(): that convenience method can CREATE an
        # absent ContentType record, which would violate this command's contract.
        payment_type_id = ContentType.objects.filter(
            app_label='core', model='payment',
        ).values_list('pk', flat=True).first()
        order_ids = sorted({order.pk for order in orders})
        target_payment_ids = {payment.pk for payment in latest} if recent is not None else set()
        order_rows = list(
            Order.objects.filter(pk__in=order_ids)
            .select_related('visit')
            .prefetch_related('items', 'discounts', 'payments')
            .order_by('pk')
        )
        visit_ids = {row.visit_id for row in order_rows if row.visit_id}
        if options['visit_code'] is not None:
            visit_ids.add(visit.pk)
        visits = list(HubVisit.objects.filter(pk__in=visit_ids).order_by('pk'))
        # A visit inspection must include every order, not only those selected
        # through the recent-payment or individual-payment entry points.
        if visits:
            related_orders = list(
                Order.objects.filter(visit_id__in=visit_ids)
                .prefetch_related('items', 'discounts', 'payments').order_by('pk')
            )
            all_orders = {row.pk: row for row in related_orders}
        else:
            all_orders = {}
        all_orders.update({row.pk: row for row in order_rows})

        findings = []
        seen_payments = set()

        def issue(code, payment_id=None, order_id=None):
            findings.append({'code': code, 'payment_id': payment_id, 'order_id': order_id})

        payment_rows = []
        for order in sorted(all_orders.values(), key=lambda row: row.pk):
            for payment in sorted(order.payments.all(), key=lambda row: row.pk):
                if payment.pk in seen_payments:
                    continue
                # For --recent and --payment-id, include only the selected
                # collections; visit totals are still computed from ALL payments.
                if options['visit_code'] is None:
                    if options['payment_id'] is not None and payment.pk != options['payment_id']:
                        continue
                    if options['order_id'] is not None and order.pk != options['order_id']:
                        continue
                    if recent is not None and payment.pk not in target_payment_ids:
                        continue
                seen_payments.add(payment.pk)
                collectible = payment.is_active and not payment.is_reversed and payment.method in (
                    Payment.Method.CASH, Payment.Method.MANUAL_TRANSFER,
                )
                movements = list(CashMovement.objects.filter(
                    related_payment=payment, is_cancelled=False,
                ).order_by('pk'))
                batches = list(PostingBatch.objects.filter(
                    operation_type='order_payment.collect',
                    source_content_type_id=payment_type_id,
                    source_object_id=str(payment.pk),
                ).order_by('created_at', 'pk')) if payment_type_id else []
                if collectible:
                    if len(batches) != 1 or batches[0].status != PostingBatch.Status.POSTED:
                        issue('collection_journal_missing_or_invalid', payment.pk, order.pk)
                    else:
                        entries = list(PostingEntry.objects.filter(batch=batches[0]).select_related('account'))
                        debits = [entry for entry in entries if entry.debit is not None]
                        credits = [entry for entry in entries if entry.credit is not None]
                        amount = Decimal(payment.amount_syp)
                        if (len(entries) != 2 or len(debits) != 1 or len(credits) != 1
                                or debits[0].debit != amount or credits[0].credit != amount):
                            issue('collection_journal_amount_mismatch', payment.pk, order.pk)
                        else:
                            if payment.method == Payment.Method.CASH and debits[0].account.scope != 'cashbox':
                                issue('cash_journal_wrong_account', payment.pk, order.pk)
                    if payment.method == Payment.Method.CASH:
                        if (len(movements) != 1 or movements[0].direction != CashMovement.Direction.IN
                                or movements[0].amount_syp != Decimal(payment.amount_syp)
                                or movements[0].related_order_id != order.pk):
                            issue('cash_movement_missing_or_mismatch', payment.pk, order.pk)
                    elif movements:
                        issue('noncash_collection_has_cash_movement', payment.pk, order.pk)
                    if order.status == Order.Status.CANCELLED:
                        issue('cancelled_order_has_active_collection', payment.pk, order.pk)
                payment_rows.append({
                    'id': payment.pk,
                    'order_id': order.pk,
                    'visit_code': str(order.visit.public_code) if order.visit_id else None,
                    'amount_syp': payment.amount_syp,
                    'method': payment.method,
                    'active': payment.is_active,
                    'reversed': payment.is_reversed,
                    'counts_as_paid': collectible,
                    'cash_movements': len(movements),
                    'collection_journals': len(batches),
                    'posting_dates': [str(batch.business_date) for batch in batches],
                })

        account_rows = []
        for visit in visits:
            children = [row for row in all_orders.values() if row.visit_id == visit.pk]
            financial = visit_financials(visit, orders=[
                row for row in children if row.status != Order.Status.CANCELLED
            ])
            # These are intentionally compared with the model properties used
            # by customer and staff views, rather than just trusting one surface.
            if ((financial['total'], financial['paid'], financial['remaining'])
                    != (visit.gross_syp, visit.paid_syp, visit.remaining_syp)):
                issue('visit_balance_projection_mismatch')
            account_rows.append({
                'visit_code': str(visit.public_code),
                'status': visit.status,
                'total_syp': financial['total'],
                'paid_syp': financial['paid'],
                'remaining_syp': financial['remaining'],
                'orders': len(children),
            })
        selected_order = None
        if options['order_id'] is not None:
            target = next(row for row in order_rows if row.pk == options['order_id'])
            selected_order = {
                'order_id': target.pk,
                'display_number': target.display_number,
                'cashier_group': 'visit' if target.visit_id else 'standalone',
                'visit_code': str(target.visit.public_code) if target.visit_id else None,
                'table_assigned': bool(target.table_id),
                'status': target.status,
                'total_syp': target.total_syp,
                'paid_syp': target.paid_syp,
                'remaining_syp': target.remaining_syp,
            }
        standalone_rows = []
        for order in order_rows:
            if order.visit_id is None:
                standalone_rows.append({
                    'order_id': order.pk,
                    'status': order.status,
                    'total_syp': order.total_syp,
                    'paid_syp': order.paid_syp,
                    'remaining_syp': order.remaining_syp,
                })
        internet = list(
            InternetSession.objects.filter(
                Q(visit_id__in=visit_ids) | Q(linked_order_id__in=all_orders)
            ).distinct().order_by('pk').values(
                'pk', 'visit_id', 'linked_order_id', 'status',
                'billing_mode', 'network_status',
            )
        )
        internet_rows = []
        for session in internet:
            linked = all_orders.get(session['linked_order_id'])
            internet_rows.append({
                'session_id': session['pk'],
                'linked_order_id': session['linked_order_id'],
                'session_status': session['status'],
                'billing_mode': session['billing_mode'],
                'network_status': session['network_status'],
                'order_payment_status': (
                    'paid' if linked and linked.remaining_syp == 0 else
                    'unpaid' if linked else 'not_billed'
                ),
            })

        output = {
            'status': 'WARN' if findings else 'PASS',
            'mode': ('payment' if options['payment_id'] is not None else
                     'order' if options['order_id'] is not None else
                     'visit' if options['visit_code'] is not None else 'recent'),
            'selected_order': selected_order,
            'accounts': account_rows,
            'standalone_orders': standalone_rows,
            'payments': payment_rows,
            'internet': internet_rows,
            'findings': findings,
            'read_only': True,
        }
        if options['as_json']:
            self.stdout.write(json.dumps(output, ensure_ascii=False, sort_keys=True))
        else:
            self.stdout.write('CUSTOMER PAYMENT AUDIT: ' + output['status'])
            if selected_order is not None:
                self.stdout.write('Selected order: ' + str(selected_order))
            self.stdout.write('Accounts: ' + str(account_rows))
            self.stdout.write('Standalone orders: ' + str(standalone_rows))
            self.stdout.write('Payments: ' + str(payment_rows))
            self.stdout.write('Internet links: ' + str(internet_rows))
            for row in findings:
                self.stdout.write('WARN [' + row['code'] + '] ' + str(row))
        return None
