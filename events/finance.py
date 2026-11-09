"""Attach already approved Hub expenses and report *known* event cash flow.

This does not post, pay, approve or reverse any Expense/Payment and does not
allocate Habaq or external-project funds into Hub operations.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from accounts.permissions import is_owner_or_admin
from core.models import ActivityLog, Expense, PostingBatch
from events.models import Event, EventExpenseAllocation
from reservations.finance_links import _verified_payment
from reservations.models import ReservationTicketPaymentLink


@transaction.atomic
def allocate_existing_event_expense(event_id, expense_id, amount_syp, rationale, *, actor):
    if not getattr(actor, 'is_active', False) or not is_owner_or_admin(actor):
        raise ValidationError({'actor': 'ربط المصروفات بالفعالية يتطلب صلاحية الإدارة.'})
    event = Event.objects.select_for_update().get(pk=event_id)
    expense = Expense.objects.select_for_update().get(pk=expense_id)
    if expense.status not in {Expense.Status.APPROVED, Expense.Status.PAID}:
        raise ValidationError({'expense': 'يجب أن يكون المصروف معتمداً أو مدفوعاً، وغير ملغى.'})
    if expense.posting_state in {Expense.PostingState.REVERSED, Expense.PostingState.CANCELLED}:
        raise ValidationError({'expense': 'لا يمكن ربط مصروف معكوس أو ملغى.'})
    if expense.paid_from == Expense.PaidFrom.EXTERNAL:
        raise ValidationError({'expense': 'مصروفات المشاريع الخارجية تحتاج تسوية منفصلة، ولا تدخل في حسابات هَب تلقائياً.'})
    if isinstance(amount_syp, bool) or not str(amount_syp).strip().isdecimal():
        raise ValidationError({'amount_syp': 'المبلغ المخصص غير صالح.'})
    amount = int(amount_syp)
    if amount < 1 or amount > expense.amount_syp:
        raise ValidationError({'amount_syp': 'المبلغ يجب أن يكون موجباً ولا يتجاوز أصل المصروف.'})
    rationale = str(rationale or '').strip()
    if not 3 <= len(rationale) <= 200:
        raise ValidationError({'rationale': 'اكتب سبب ارتباط المصروف بالفعالية (3–200 حرف).'})
    old = EventExpenseAllocation.objects.select_for_update().filter(
        event=event, expense=expense,
    ).first()
    if old:
        if old.amount_syp == amount and old.rationale == rationale:
            return old, False
        raise ValidationError({'expense': 'المصروف مرتبط بهذه الفعالية بالفعل. يلزم إجراء تصحيح إداري مُوثق.'})
    allocated = EventExpenseAllocation.objects.filter(expense=expense).aggregate(
        total=Sum('amount_syp'))['total'] or 0
    if allocated + amount > expense.amount_syp:
        raise ValidationError({'amount_syp': 'المبلغ الموزّع على الفعاليات يتجاوز أصل المصروف.'})
    link = EventExpenseAllocation(
        event=event, expense=expense, amount_syp=amount,
        rationale=rationale, allocated_by=actor,
    )
    link.full_clean()
    link.save()
    ActivityLog.objects.create(
        actor=actor, action='event.expense_allocated',
        details={
            'event_id': event.pk, 'expense_id': expense.pk,
            'allocation_id': link.pk, 'amount_syp': amount,
        },
    )
    return link, True


def event_known_finance_report(event_id):
    """Do not confuse this partial cash picture with complete profitability."""
    links = ReservationTicketPaymentLink.objects.filter(
        reservation__event_id=event_id,
    ).select_related('payment', 'reservation')
    verified_receipts = 0
    receipts_count = 0
    cancelled_booking_receipts = 0
    for link in links:
        if _verified_payment(link.payment):
            verified_receipts += link.payment.amount_syp
            receipts_count += 1
            if link.reservation.status == 'cancelled':
                cancelled_booking_receipts += link.payment.amount_syp

    allocations = list(
        EventExpenseAllocation.objects.filter(event_id=event_id)
        .select_related('expense', 'expense__payment_batch')
        .order_by('expense__business_date', 'pk')
    )
    allocated_total = 0
    verified_paid_costs = 0
    not_verified_paid = 0
    for link in allocations:
        expense = link.expense
        allocated_total += link.amount_syp
        if (
            expense.status == Expense.Status.PAID
            and expense.posting_state == Expense.PostingState.POSTED
            and expense.payment_batch_id
            and expense.payment_batch.status == PostingBatch.Status.POSTED
            and expense.payment_batch.is_balanced()
        ):
            verified_paid_costs += link.amount_syp
        else:
            not_verified_paid += link.amount_syp

    return {
        'verified_receipts_syp': verified_receipts,
        'receipt_count': receipts_count,
        'cancelled_booking_receipts_syp': cancelled_booking_receipts,
        'allocated_cost_total_syp': allocated_total,
        'verified_paid_costs_syp': verified_paid_costs,
        'allocated_unverified_costs_syp': not_verified_paid,
        'known_cash_difference_syp': verified_receipts - verified_paid_costs,
        'allocations': allocations,
        'is_complete_profit_and_loss': False,
    }
