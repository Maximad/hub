"""Authorized, phone-friendly physical stock counts.

This is an operational count/correction, not a purchase receipt, cash expense,
or automatic financial opening-balance journal.
"""
from decimal import Decimal, InvalidOperation
import uuid

from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.permissions import require_staff_capability
from core.models import ActivityLog, AuditEvent, InventoryItem, PostingCommand, StockMovement


SIGNING_SALT = 'hub.staff.inventory.stock_count.v1'
SCOPE = 'inventory.stock_count'


def _quantity(raw):
    if raw is None or str(raw).strip() == '':
        raise ValidationError('أدخل الكمية التي وجدتها فعلياً. اكتب 0 إذا كانت المادة غير موجودة.')
    try:
        amount = Decimal(str(raw).strip())
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError('أدخل كمية رقمية صحيحة.')
    if not amount.is_finite() or amount < 0:
        raise ValidationError('يجب أن تكون الكمية صفراً أو رقماً موجباً.')
    if amount.as_tuple().exponent < -3 or amount >= Decimal('1000000000'):
        raise ValidationError('استخدم حتى ثلاث منازل عشرية وكمية ضمن الحدود المتاحة.')
    return amount.quantize(Decimal('0.001'))


def _preview_token(user, item, counted, reason):
    return signing.dumps({
        'actor': user.pk,
        'item': item.pk,
        'before': str(item.current_quantity),
        'after': str(counted),
        'unit': item.unit,
        'reason': reason,
        'nonce': uuid.uuid4().hex,
    }, salt=SIGNING_SALT)


def _page(request, *, item=None, preview=None, error='', values=None):
    q = (request.GET.get('q') or request.POST.get('q') or '').strip()[:80]
    items = InventoryItem.objects.filter(is_active=True)
    if q:
        items = items.filter(
            Q(name_ar__icontains=q) | Q(name_en__icontains=q) | Q(code__icontains=q)
        )
    items = list(items.order_by('name_ar', 'pk')[:60])
    if not item:
        selected = request.GET.get('item') or request.POST.get('item')
        if selected and str(selected).isdigit():
            item = InventoryItem.objects.filter(is_active=True, pk=int(selected)).first()
    return render(request, 'staff/inventory_stock_count.html', {
        'items': items,
        'item': item,
        'preview': preview,
        'error': error,
        'values': values or {},
        'q': q,
    })


@require_staff_capability('inventory_stock_count')
@require_http_methods(['GET', 'POST'])
def staff_inventory_stock_count(request):
    if request.method == 'GET':
        return _page(request)

    action = request.POST.get('action')
    if action == 'preview':
        item = get_object_or_404(
            InventoryItem, pk=request.POST.get('item'), is_active=True,
        )
        raw = request.POST.get('count')
        reason = request.POST.get('reason', '').strip()[:1000]
        try:
            counted = _quantity(raw)
            difference = counted - item.current_quantity
            if difference and not reason:
                raise ValidationError('أدخل سبب الاختلاف قبل تأكيد تعديل المخزون.')
            token = _preview_token(request.user, item, counted, reason)
            return _page(request, item=item, preview={
                'before': item.current_quantity,
                'after': counted,
                'difference': difference,
                'unit': item.get_unit_display(),
                'reason': reason,
                'token': token,
            }, values={'count': raw, 'reason': reason})
        except ValidationError as error:
            return _page(request, item=item, error=' '.join(error.messages),
                         values={'count': raw, 'reason': reason})

    if action != 'confirm':
        return _page(request, error='عملية غير معروفة.')

    try:
        payload = signing.loads(
            request.POST.get('confirmation_token', ''),
            salt=SIGNING_SALT, max_age=1800,
        )
        if payload.get('actor') != request.user.pk:
            raise ValidationError('هذا التأكيد يعود لمستخدم آخر.')
        item_id = int(payload['item'])
        before = _quantity(payload['before'])
        counted = _quantity(payload['after'])
        reason = str(payload.get('reason', '')).strip()[:1000]
        nonce = str(uuid.UUID(hex=payload['nonce']))
        key = f'stock-count:{nonce}'
    except (signing.BadSignature, KeyError, TypeError, ValueError, ValidationError) as error:
        return _page(request, error='انتهت صلاحية المراجعة أو تغيرت البيانات. أعد معاينة الكمية.')

    try:
        with transaction.atomic():
            item = get_object_or_404(
                InventoryItem.objects.select_for_update(),
                pk=item_id, is_active=True,
            )
            previous = PostingCommand.objects.filter(key=key).first()
            if previous:
                if previous.command != SCOPE or previous.actor_id != request.user.pk:
                    raise ValidationError('تعارض في تأكيد الجرد. أعد المحاولة.')
                messages.info(request, 'تم تسجيل الجرد سابقاً. لم تُنشأ حركة مكررة.')
                return redirect('staff_inventory_stock_count')

            if item.unit != payload['unit'] or item.current_quantity != before:
                raise ValidationError(
                    'تغير مخزون المادة منذ المراجعة. اعرض الكمية الحالية وراجع الفرق مجدداً.'
                )

            difference = counted - before
            if difference and not reason:
                raise ValidationError('تعديل المخزون يتطلب سبباً واضحاً.')
            if not difference:
                messages.success(request, 'الكمية مطابقة للمخزون المسجل، ولا حاجة لحركة تصحيح.')
                return redirect('staff_inventory_stock_count')

            movement = StockMovement(
                inventory_item=item,
                business_date=timezone.localdate(),
                movement_type=StockMovement.MovementType.CORRECTION,
                direction=StockMovement.Direction.IN if difference > 0 else StockMovement.Direction.OUT,
                quantity=abs(difference),
                unit=item.unit,
                unit_cost_syp=item.estimated_unit_cost_syp,
                total_value_syp=(
                    (abs(difference) * item.estimated_unit_cost_syp).quantize(Decimal('0.01'))
                    if item.estimated_unit_cost_syp is not None else None
                ),
                reason=reason,
                created_by=request.user,
                approved_by=request.user,
            )
            movement.full_clean()
            PostingCommand.objects.create(
                key=key,
                command=SCOPE,
                source_type='core.InventoryItem',
                source_id=str(item.pk),
                result_type='core.StockMovement',
                actor=request.user,
                channel='staff-stock-count',
                request_metadata={'previous': str(before), 'counted': str(counted)},
            )
            movement.save()
            movement.apply_to_stock()
            PostingCommand.objects.filter(key=key).update(result_id=str(movement.pk))
            AuditEvent.objects.create(
                actor=request.user,
                approver=request.user,
                action='inventory_physical_count_confirmed',
                source_content_type=ContentType.objects.get_for_model(StockMovement),
                source_object_id=str(movement.pk),
                before_snapshot={'item_id': item.pk, 'quantity': str(before), 'unit': item.unit},
                after_snapshot={'quantity': str(counted), 'delta': str(difference),
                                'reason': reason, 'movement_id': movement.pk},
                request_key=key,
                channel='staff-stock-count',
            )
            ActivityLog.objects.create(
                actor=request.user, action='inventory_stock_count_confirmed',
                details={'item_id': item.pk, 'movement_id': movement.pk,
                         'before': str(before), 'counted': str(counted),
                         'difference': str(difference)},
            )
    except ValidationError as error:
        return _page(request, error=' '.join(getattr(error, 'messages', [str(error)])))
    messages.success(request, 'تم تأكيد الجرد وتحديث الكمية المسجلة للمادة.')
    return redirect('staff_inventory_stock_count')
