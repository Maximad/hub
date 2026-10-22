"""Recoverable, exactly-once staff collection for standalone orders.

Visit-account payments remain owned by visit_settlement. No separate ledger or
cash posting path is introduced here.
"""
import hashlib
import logging

from django.contrib import messages
from django.contrib.auth import authenticate
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone

from accounts.permissions import can_approve_partial_payment
from core.currency_forms import CurrencyEntryFormService
from core.models import ActivityLog, Order, Payment, PostingCommand
from core.notifications import create_notification
from core.services.posting.context import PostingContext
from core.services.posting.order_payments import collect as collect_order_payment

logger = logging.getLogger(__name__)


def _request_key(request, order):
    """Preserve explicit form/header tokens and the existing legacy fallback."""
    if request.headers.get('Idempotency-Key') or request.POST.get('idempotency_key'):
        return request.headers.get('Idempotency-Key') or request.POST['idempotency_key']
    payload = repr(sorted((key, request.POST.getlist(key)) for key in request.POST))
    digest = hashlib.sha256(payload.encode()).hexdigest()[:24]
    return f'cashier:{order.pk}:{request.user.pk}:{digest}'


def _submitted_values(request):
    # Do not retain CSRF, passwords, or manager credentials in rendered state.
    values = {name: request.POST.get(name, '') for name in (
        'currency_currency', 'currency_acknowledged',
        'currency_allow_stale_rate', 'method', 'notes',
    )}
    values['currency_amount'] = request.POST.get('currency_amount') or request.POST.get('amount_syp', '')
    return values


def recovery_context(request, order, remaining):
    """Shared by the full page and contextual drawer; failures retain the key."""
    import uuid
    recovery = getattr(request, '_standalone_payment_recovery', None)
    values = recovery['values'] if recovery else {
        'currency_amount': remaining, 'method': Payment.Method.CASH,
    }
    return {
        'form_values': values,
        'payment_recovery': bool(recovery),
        'collection_request_key': recovery['key'] if recovery else f'order-pay:{order.pk}:{uuid.uuid4().hex}',
    }


def _fingerprint(values):
    """Compare customer payment intent, excluding staff secrets and CSRF."""
    return hashlib.sha256(repr(sorted(values.items())).encode()).hexdigest()


def _completed_receipt(order, key, user, fingerprint):
    """Never accept another order's key or a changed/unauthorized replay."""
    receipt = PostingCommand.objects.select_for_update().filter(key=key).first()
    if not receipt:
        return None
    if (receipt.command != 'order_payment.collect'
            or receipt.source_type != order._meta.label
            or receipt.source_id != str(order.pk)
            or receipt.result_type != Payment._meta.label
            or receipt.actor_id != user.pk
            or receipt.request_metadata.get('cashier_payment_fingerprint') != fingerprint):
        raise ValidationError('مرجع الدفع مستخدم لطلب آخر أو تغيرت تفاصيل الدفع. حدّث صفحة الحساب.')
    payment = Payment.objects.filter(pk=receipt.result_id, order_id=order.pk).first()
    if not payment:
        raise ValidationError('تعذر مطابقة إيصال الدفعة مع الطلب. راجع سجل الدفعات.')
    if not payment.is_active or payment.is_reversed:
        raise ValidationError('سُجلت هذه الدفعة ثم عُكست أو أُلغيت. راجع سجل الدفعات.')
    return payment


def _notify_partial_approval(request, order, amount):
    for kind, title, body in (
        ('manager_approval_needed', 'مطلوب موافقة المدير',
         f'دفع جزئي يحتاج موافقة — {order.display_number}'),
        ('partial_payment_requested', 'دفع جزئي يحتاج موافقة',
         f'{amount} ل.س — {order.display_number}'),
    ):
        try:
            create_notification(kind, title, body, order=order,
                                target_role='admin', created_by=request.user)
        except Exception:
            logger.exception('Standalone partial payment approval notification failed')


def collect_standalone_payment(request, public_code):
    """Atomic posting, currency evidence and approval log with safe POST retries.

    The surrounding legacy cashier view enforces the cashier capability.
    """
    from core.views_legacy import staff_cashier_order

    if request.method != 'POST':
        from django.http import Http404
        raise Http404()

    order = get_object_or_404(Order, public_code=public_code)
    key = _request_key(request, order)
    values = _submitted_values(request)
    fingerprint = _fingerprint(values)
    approval_needed = False
    pending_notification = False
    amount_for_notice = None

    try:
        with transaction.atomic():
            # Same row lock used by order_payments.collect; lock before balances.
            order = Order.objects.select_for_update().get(pk=order.pk)
            receipt = _completed_receipt(order, key, request.user, fingerprint)
            if receipt:
                result_message = 'هذه الدفعة مسجلة بالفعل. لم تُنشأ دفعة أو حركة صندوق إضافية.'
            else:
                if order.visit_id:
                    raise ValidationError('أصبح الطلب تابعاً لحساب جلسة. افتح الحساب المجمع.')
                if order.status == Order.Status.CANCELLED:
                    raise ValidationError('الطلب ملغى ولا يقبل دفعات جديدة.')

                service = CurrencyEntryFormService(request, operation='payment')
                entry = service.clean(request.POST.get('amount_syp'))
                amount = entry.base_amount
                amount_for_notice = amount
                remaining = order.remaining_syp
                if amount <= 0:
                    raise ValidationError('المبلغ يجب أن يكون أكبر من صفر.')
                if amount > remaining:
                    raise ValidationError('المبلغ لا يجوز أن يتجاوز المتبقي.')
                method = values['method']
                if method not in {Payment.Method.CASH, Payment.Method.MANUAL_TRANSFER}:
                    raise ValidationError('اختر طريقة دفع محصلة صالحة.')

                approver = None
                if amount < remaining:
                    approver = request.user if can_approve_partial_payment(request.user) else None
                    if approver is None:
                        candidate = authenticate(
                            request, username=request.POST.get('manager_username', '').strip(),
                            password=request.POST.get('manager_password', ''),
                        )
                        if candidate and can_approve_partial_payment(candidate):
                            approver = candidate
                    if approver is None:
                        approval_needed = True
                        raise PermissionDenied('الدفع الجزئي يحتاج موافقة المدير أو صاحب المحل.')

                payment = collect_order_payment(order, PostingContext(
                    actor=request.user, approver=approver,
                    business_date=timezone.localdate(),
                    idempotency_key=key, channel='cashier',
                    request_metadata={'path': request.path,
                                      'cashier_payment_fingerprint': fingerprint},
                ), amount, method, values['notes'].strip())
                service.snapshot(payment, entry, 'amount_syp')
                if approver:
                    ActivityLog.objects.create(actor=request.user, action='partial_payment_approved',
                        details={'order_display_number': order.display_number,
                                 'order_public_code': str(order.public_code),
                                 'payment_amount': str(amount),
                                 'remaining_before_payment': remaining,
                                 'approving_manager_username': approver.username,
                                 'cashier_username': request.user.username})
                pending_notification = order.remaining_syp > 0
                result_message = f'تم تسجيل دفعة بقيمة {amount} ل.س.'

        messages.success(request, result_message)
        if pending_notification:
            try:
                create_notification(
                    'payment_pending', f'الدفع بانتظار الكاشير {order.display_number}',
                    f'المتبقي {order.remaining_syp} ل.س', order=order,
                    target_role='cashier', created_by=request.user,
                )
            except Exception:
                logger.exception('Standalone payment reminder notification failed')
    except (ValidationError, PermissionDenied) as error:
        if approval_needed:
            _notify_partial_approval(request, order, amount_for_notice)
        messages.error(request, ' '.join(getattr(error, 'messages', [str(error)])))
        request._standalone_payment_recovery = {'key': key, 'values': values}
        return staff_cashier_order(request, public_code)
    except Exception:
        logger.exception('Standalone payment save failed')
        messages.error(request, 'تعذر تأكيد حفظ الدفعة. راجع الرصيد وسجل الدفعات قبل إعادة المحاولة من النموذج نفسه.')
        request._standalone_payment_recovery = {'key': key, 'values': values}
        return staff_cashier_order(request, public_code)

    return redirect('staff_cashier_order', public_code=order.public_code)
