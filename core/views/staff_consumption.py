"""Staff POS classification and owner-only informational consumption statement."""
import uuid
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import require_staff_capability, can_approve_partial_payment
from core.models import Order, OrderConsumption
from core.services.order_consumption import can_record_consumption, record_consumption


@require_staff_capability('cashier')
@require_POST
def staff_order_consumption(request, public_code):
    order = get_object_or_404(Order, public_code=public_code)
    try:
        item_id = int(request.POST.get('order_item_id', ''))
        result, created = record_consumption(
            actor=request.user, order_id=order.pk, order_item_id=item_id,
            category=request.POST.get('category', ''),
            quantity=request.POST.get('quantity', ''),
            reason=request.POST.get('reason', ''),
            beneficiary_note=request.POST.get('beneficiary_note', ''),
            owner_treatment=request.POST.get('owner_treatment', ''),
            request_key=request.POST.get('request_key', ''),
        )
    except (ValidationError, ValueError, PermissionDenied, Order.DoesNotExist) as error:
        messages.error(request, ' '.join(getattr(error, 'messages', [str(error)])))
    else:
        if created:
            messages.success(request, f'تم تسجيل {result.get_category_display()} وتحديث المبلغ المستحق دون دفعة نقدية.')
        else:
            messages.info(request, 'هذه الضيافة مسجلة مسبقاً. لم يُضَف خصم جديد.')
    return redirect('staff_cashier_order', public_code=order.public_code)


@require_staff_capability('reports')
def staff_consumption_report(request):
    if not request.user.is_active or not request.user.is_superuser:
        raise PermissionDenied('سجل الاستهلاك الشخصي متاح لصاحب النظام فقط.')
    today = timezone.localdate()
    raw_month = request.GET.get('month', '')
    try:
        month = date.fromisoformat((raw_month or today.strftime('%Y-%m')) + '-01')
    except ValueError:
        month = today.replace(day=1)
    start = month.replace(day=1)
    end = date(start.year + (start.month == 12), (start.month % 12) + 1, 1)
    records = list(
        OrderConsumption.objects.select_related('order', 'order_item', 'created_by', 'approved_by')
        .filter(business_date__gte=start, business_date__lt=end)
        .order_by('-business_date', '-created_at')[:500]
    )
    totals = {}
    for value, label in OrderConsumption.Category.choices:
        rows = [r for r in records if r.category == value]
        totals[value] = {
            'label': label, 'count': len(rows),
            'retail': sum(r.retail_value_syp for r in rows),
            'cost': sum((r.estimated_cost_syp or Decimal('0') for r in rows), Decimal('0')),
            'missing_cost': sum(r.estimated_cost_syp is None for r in rows),
        }
    return render(request, 'staff/consumption_report.html', {
        'month': start.strftime('%Y-%m'), 'records': records,
        'totals': totals, 'report_limited': len(records) >= 500,
    })
