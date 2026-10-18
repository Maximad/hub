"""Draft-only internal consumption requests. No inventory or finance posting."""
import uuid
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import require_staff_capability, is_owner_or_admin
from core.models import ActivityLog, InternalConsumptionRequest, InventoryItem
from core.services.inventory_unit_entry import to_base_quantity, allowed_entry_units


def _permitted_category(user, category):
    if category == InternalConsumptionRequest.Category.OWNER_PERSONAL:
        return user.is_superuser
    return category in InternalConsumptionRequest.Category.values


@require_staff_capability('inventory')
def staff_internal_consumption_requests(request):
    if request.method == 'POST':
        try:
            category = request.POST.get('category', '')
            if not _permitted_category(request.user, category):
                raise PermissionDenied('الاستخدام الشخصي للمالك مخصص للمالك فقط.')
            item = get_object_or_404(InventoryItem, pk=request.POST.get('inventory_item_id'), is_active=True)
            unit = request.POST.get('unit') or item.unit
            entered = request.POST.get('quantity', '').strip()
            base = to_base_quantity(entered, item.unit, unit)
            if item.unit in {'piece', 'pack', 'bottle', 'box'} and base != base.to_integral_value():
                raise ValidationError('القطع والعبوات والزجاجات والصناديق تُسجّل بأعداد صحيحة فقط.')
            purpose = request.POST.get('purpose', '').strip()
            beneficiary_note = request.POST.get('beneficiary_note', '').strip()
            if not purpose or len(purpose) > 255 or len(beneficiary_note) > 120:
                raise ValidationError('سبب الاستهلاك مطلوب ويجب أن يكون موجزاً.')
            key = uuid.UUID(request.POST.get('request_key', ''))
            # Submitting a draft is NOT evidence of physical deduction or approval.
            with transaction.atomic():
                row, created = InternalConsumptionRequest.objects.get_or_create(
                    request_key=key,
                    defaults={
                        'inventory_item': item, 'business_date': timezone.localdate(),
                        'category': category, 'entered_quantity': Decimal(entered),
                        'entered_unit': unit, 'base_quantity': base, 'base_unit': item.unit,
                        'purpose': purpose, 'beneficiary_note': beneficiary_note,
                        'estimated_unit_cost_syp_snapshot': item.estimated_unit_cost_syp,
                        'created_by': request.user,
                    }
                )
                if not created and (row.created_by_id != request.user.pk or row.inventory_item_id != item.pk
                                    or row.category != category or row.base_quantity != base):
                    raise ValidationError('رمز تسجيل هذه العملية مستخدم. حدّث الصفحة وحاول مجدداً.')
                if created:
                    row.full_clean()
                    ActivityLog.objects.create(
                        actor=request.user, action='consumption_request_draft_created',
                        details={'request_id': row.pk, 'inventory_item_id': item.pk,
                                 'base_quantity': str(base), 'base_unit': item.unit,
                                 'category': category, 'stock_posted': False, 'finance_posted': False},
                    )
            messages.success(request, 'سُجّل طلب الاستهلاك للمراجعة فقط. لم تتغير الكميات أو القيود المالية.' if created
                             else 'طلب الاستهلاك مسجّل سابقاً دون خصم جديد.')
        except (ValidationError, ValueError, TypeError, PermissionDenied) as exc:
            messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
        return redirect('staff_internal_consumption_requests')
    items = InventoryItem.objects.filter(is_active=True).order_by('name_ar')
    rows = InternalConsumptionRequest.objects.select_related('inventory_item', 'created_by', 'reviewed_by')
    if not is_owner_or_admin(request.user):
        rows = rows.filter(created_by=request.user)
    return render(request, 'staff/internal_consumption_requests.html', {
        'items': items, 'units': InventoryItem.Unit.choices, 'rows': rows[:80],
        'request_key': uuid.uuid4(), 'is_manager': is_owner_or_admin(request.user),
    })


@require_staff_capability('inventory')
@require_POST
def staff_internal_consumption_review(request, request_id):
    if not is_owner_or_admin(request.user):
        raise PermissionDenied('مراجعة الطلبات مخصصة للإدارة.')
    with transaction.atomic():
        row = get_object_or_404(InternalConsumptionRequest.objects.select_for_update(), pk=request_id)
        if row.status != InternalConsumptionRequest.Status.SUBMITTED:
            messages.error(request, 'سبق اتخاذ إجراء بشأن هذا الطلب.')
        else:
            action = request.POST.get('action')
            note = request.POST.get('note', '').strip()
            if action not in ('review', 'cancel') or not note or len(note) > 255:
                messages.error(request, 'حدد إجراءً وسبباً واضحاً.')
            else:
                row.status = (InternalConsumptionRequest.Status.REVIEWED if action == 'review'
                              else InternalConsumptionRequest.Status.CANCELLED)
                row.review_note = note
                row.reviewed_by = request.user
                row.reviewed_at = timezone.now()
                row.save(update_fields=['status', 'review_note', 'reviewed_by', 'reviewed_at', 'updated_at'])
                ActivityLog.objects.create(
                    actor=request.user, action='consumption_request_reviewed_without_posting',
                    details={'request_id': row.pk, 'status': row.status,
                             'stock_posted': False, 'finance_posted': False},
                )
                messages.success(request, 'تمت مراجعة الطلب دون أي خصم مخزون أو قيد مالي.')
    return redirect('staff_internal_consumption_requests')
