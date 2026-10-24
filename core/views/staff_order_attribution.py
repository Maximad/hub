"""Correct descriptive order recipient metadata without touching commercial treatment.

Creation provenance (entered_by, entry_source) is immutable. Financial
classification remains solely in approved OrderConsumption / OrderDiscount.
"""
from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from accounts.permissions import require_staff_capability
from core.models import ActivityLog, Order


@require_staff_capability('cashier')
@require_POST
def staff_order_recipient_update(request, public_code):
    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), public_code=public_code)
        kind = (request.POST.get('recipient_kind') or '').strip()
        note = (request.POST.get('recipient_note') or '').strip()
        if kind not in Order.RecipientKind.values or len(note) > 120:
            messages.error(request, 'نوع المستفيد أو الاسم غير صالح. لم يتغير الطلب.')
        elif (kind, note) == (order.recipient_kind, order.recipient_note):
            messages.info(request, 'لم يتغير توصيف المستفيد.')
        else:
            old = {'kind': order.recipient_kind, 'note': order.recipient_note}
            order.recipient_kind = kind
            order.recipient_note = note
            order.save(update_fields=['recipient_kind', 'recipient_note', 'updated_at'])
            ActivityLog.objects.create(actor=request.user, action='order.recipient_metadata_changed', details={
                'order_id': order.pk,
                'old': old,
                'new': {'kind': kind, 'note': note},
                'financial_change': False,
            })
            messages.success(request, 'تم حفظ توصيف المستفيد دون أي تغيير مالي.')
    return redirect('staff_order_edit', public_code=public_code)
