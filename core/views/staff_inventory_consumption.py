"""Arabic direct consumption: request, confirm physical issue, return and report."""
import uuid
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, Sum, Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.permissions import require_staff_capability, is_owner_or_admin
from core.models import InventoryConsumptionRequest, InventoryItem, OrderConsumption
from core.services.inventory_unit_entry import allowed_entry_units
from core.services.inventory_consumption_requests import (
    allowed_categories, request_inventory_consumption, cancel_consumption_request,
    review_consumption_request, can_review_consumption_requests,
    issue_consumption, reverse_consumption,
)


def _page(request):
    q = (request.GET.get('q') or '').strip()[:70]
    selection = request.GET.get('item') or ''
    items = InventoryItem.objects.filter(is_active=True)
    if q:
        items = items.filter(Q(name_ar__icontains=q) | Q(name_en__icontains=q) | Q(code__icontains=q))
    items = list(items.order_by('name_ar')[:80])
    item = None
    if selection.isdigit():
        item = InventoryItem.objects.filter(pk=int(selection), is_active=True).first()
    units = [(value, label) for value, label in InventoryItem.Unit.choices
             if item and value in allowed_entry_units(item.unit)]
    categories = [
        (value, label) for value, label in InventoryConsumptionRequest.Category.choices
        if value in allowed_categories(request.user)
    ]
    rows = InventoryConsumptionRequest.objects.select_related('inventory_item', 'requested_by', 'reviewed_by', 'issue_movement__approved_by', 'reversal_movement')
    if not request.user.is_superuser:
        rows = rows.exclude(category=InventoryConsumptionRequest.Category.OWNER_PERSONAL)
        if not is_owner_or_admin(request.user):
            rows = rows.filter(requested_by=request.user)
    month = request.GET.get('month') or timezone.localdate().strftime('%Y-%m')
    try:
        month_start = date.fromisoformat(month + '-01')
        month_end = date(month_start.year + (month_start.month == 12), month_start.month % 12 + 1, 1)
    except (ValueError, TypeError):
        month_start = timezone.localdate().replace(day=1)
        month_end = date(month_start.year + (month_start.month == 12), month_start.month % 12 + 1, 1)
    month = month_start.strftime('%Y-%m')
    totals = {}
    fields = ('category', 'inventory_item_id', 'inventory_item__name_ar', 'base_unit')
    # Movement dates, not draft dates; a later return appears in its own month.
    for movement, prefix in [('issue_movement', 'issued'), ('reversal_movement', 'returned')]:
        groups = rows.filter(**{movement + '__business_date__gte': month_start,
                                movement + '__business_date__lt': month_end}).values(*fields).annotate(
            qty=Sum('base_quantity'), cost=Sum('total_cost_estimate_syp'),
            unknown=Count('pk', filter=Q(total_cost_estimate_syp__isnull=True)))
        for group in groups:
            key = (group['category'], group['inventory_item_id'], group['base_unit'])
            entry = totals.setdefault(key, dict(name=group['inventory_item__name_ar'],
                category=dict(InventoryConsumptionRequest.Category.choices)[group['category']],
                unit=dict(InventoryItem.Unit.choices)[group['base_unit']],
                issued=Decimal('0'), returned=Decimal('0'), cost=Decimal('0'), unknown=0))
            entry[prefix] += group['qty']
            entry['cost'] += (group['cost'] or Decimal('0')) * (1 if prefix == 'issued' else -1)
            entry['unknown'] += group['unknown']
    for entry in totals.values():
        entry['net'] = entry['issued'] - entry['returned']
    rows = list(rows.order_by('-created_at')[:80])
    for record in rows:
        record.can_issue = (can_review_consumption_requests(request.user)
            and record.state in (record.State.PENDING, record.State.REVIEWED)
            and not (record.category == record.Category.STAFF and record.requested_by_id == request.user.pk))
        record.can_return = (can_review_consumption_requests(request.user)
            and record.state == record.State.ISSUED
            and not (record.category == record.Category.STAFF and record.requested_by_id == request.user.pk))
    return render(request, 'staff/inventory_consumption_requests.html', {
        'month': month, 'consumption_totals': list(totals.values()),
        'q': q, 'items': items, 'item': item, 'units': units,
        'can_review': can_review_consumption_requests(request.user),
        'categories': categories, 'rows': rows,
        'owner_treatments': OrderConsumption.OwnerTreatment.choices,
        'request_key': uuid.uuid4(), 'today': timezone.localdate(),
    })


@require_staff_capability('inventory')
@require_http_methods(['GET', 'POST'])
def staff_inventory_consumption_requests(request):
    if request.method == 'GET':
        return _page(request)
    # View-As must not submit drafts as though the impersonated user did so.
    if getattr(request, 'view_as_actor', None):
        raise PermissionDenied('تعذر التسجيل أثناء معاينة حساب مستخدم آخر.')
    action = request.POST.get('action')
    try:
        if action == 'create':
            record, created = request_inventory_consumption(
                actor=request.user, item_id=request.POST.get('inventory_item'),
                category=request.POST.get('category', ''),
                quantity=request.POST.get('quantity', ''),
                unit=request.POST.get('unit', ''),
                reason=request.POST.get('reason', ''),
                request_key=request.POST.get('request_key', ''),
                beneficiary_note=request.POST.get('beneficiary_note', ''),
                event_label=request.POST.get('event_label', ''),
                owner_treatment=request.POST.get('owner_treatment', ''),
            )
            messages.success(
                request,
                ('سُجّل الطلب بانتظار تأكيد الصرف. لم تُخصم كمية بعد.'
                 if created else 'هذا الطلب مسجل سابقاً؛ لم تُضف حركة جديدة.'),
            )
        elif action in ('issue', 'return'):
            record = get_object_or_404(InventoryConsumptionRequest, pk=request.POST.get('request_id'))
            if action == 'issue':
                _, changed = issue_consumption(actor=request.user, request_id=record.pk,
                    direct_issue_confirmed=request.POST.get('direct_issue_confirmed') == 'yes')
                messages.success(request, 'تم تأكيد الصرف وخصم المخزون.' if changed else 'سُجّل الصرف سابقاً. لم يتكرر الخصم.')
            else:
                _, changed = reverse_consumption(actor=request.user, request_id=record.pk,
                    kind=request.POST.get('kind', ''), reason=request.POST.get('reason', ''),
                    restoration_confirmed=request.POST.get('restoration_confirmed') == 'yes')
                messages.success(request, 'أعيدت الكمية إلى المخزون مع حفظ سجل الصرف الأصلي.' if changed else 'سُجّل الإرجاع سابقاً.')
        elif action == 'review':
            record = get_object_or_404(InventoryConsumptionRequest, pk=request.POST.get('request_id'))
            review_consumption_request(actor=request.user, request_id=record.pk,
                                       note=request.POST.get('review_note', ''))
            messages.success(request, 'تمت المراجعة فقط. لم يُخصم مخزون ولم يُنشأ قيد مالي.')
        elif action == 'cancel':
            record = get_object_or_404(
                InventoryConsumptionRequest, pk=request.POST.get('request_id')
            )
            _record, cancelled = cancel_consumption_request(
                actor=request.user, request_id=record.pk,
                reason=request.POST.get('cancellation_reason', ''),
            )
            messages.success(request, 'أُلغي الطلب دون أي أثر على المخزون.' if cancelled
                             else 'كان الطلب ملغى مسبقاً.')
        else:
            raise ValidationError('إجراء غير معروف.')
    except (ValidationError, ValueError) as error:
        messages.error(request, ' '.join(getattr(error, 'messages', [str(error)])))
    return redirect('staff_inventory_consumption_requests')
