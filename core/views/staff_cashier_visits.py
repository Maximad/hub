import hashlib
import logging
import uuid

from django.contrib import messages
from django.contrib.auth import authenticate
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from accounts.permissions import can_approve_partial_payment, require_staff_capability
from core.currency_forms import CurrencyEntryFormService
from core.models import ActivityLog, HubVisit, InternetSession, Order, Payment
from core.notifications import create_notification
from core.services.internet_access import end_usage_session
from core.services.posting.context import PostingContext
from core.services.posting.policy import is_finance_user
from core.services.visit_internet import finalize_visit_metered_session
from core.services.visit_settlement import (
    COLLECTIBLE_PAYMENT_METHODS,
    active_visit_orders,
    allocate_visit_payment,
    visit_financials,
    visit_payment_receipt,
)
from core.settings_helpers import get_page_setting, get_system_settings
from core.services.visits import customer_visit_queryset
from reservations.models import Reservation
from reservations.services import complete_reservation_for_visit


def _visit_queryset():
    order_qs = (
        Order.objects.exclude(status=Order.Status.CANCELLED)
        .select_related('table', 'table__room')
        .prefetch_related('items', 'payments', 'discounts')
        .order_by('created_at', 'pk')
    )
    return (
        customer_visit_queryset(HubVisit.objects.select_related('table', 'table__room', 'member'))
        .prefetch_related(Prefetch('orders', queryset=order_qs, to_attr='cashier_orders'))
    )


def _visit_row(visit):
    financial = visit_financials(visit, orders=getattr(visit, 'cashier_orders', None))
    return {'visit': visit, **financial}


def _standalone_rows(query=''):
    qs = (
        Order.objects.filter(visit__isnull=True)
        .exclude(status=Order.Status.CANCELLED)
        .select_related('table', 'table__room')
        .prefetch_related('items', 'payments', 'discounts')
        .order_by('-created_at')
    )
    if query:
        qs = qs.filter(
            Q(table__name_ar__icontains=query)
            | Q(table__room__name_ar__icontains=query)
            | Q(delivery_customer_name__icontains=query)
            | Q(delivery_phone__icontains=query)
        )
    rows = []
    for order in qs[:100]:
        rows.append({
            'order': order,
            'total': order.total_syp,
            'paid': order.paid_syp,
            'remaining': order.remaining_syp,
        })
    return rows


def _digest_for_request(request, prefix):
    payload = repr(sorted((key, request.POST.getlist(key)) for key in request.POST))
    digest = hashlib.sha256(payload.encode()).hexdigest()[:24]
    return request.headers.get('Idempotency-Key') or request.POST.get('idempotency_key') or f'{prefix}:{request.user.pk}:{digest}'


class PartialApprovalRequired(PermissionDenied):
    def __init__(self, visit, amount, anchor_order):
        super().__init__('الدفع الجزئي يحتاج موافقة مدير.')
        self.visit, self.amount, self.anchor_order = visit, amount, anchor_order

    def notify(self, request):
        label = str(self.visit.table) if self.visit.table_id else f'جلسة {self.visit.display_number}'
        for event, title, body in (
            ('manager_approval_needed', 'مطلوب موافقة المدير', f'دفع جزئي لحساب {label}'),
            ('partial_payment_requested', 'دفع جزئي يحتاج موافقة', f'{self.amount} ل.س — {label}'),
        ):
            create_notification(event, title, body, order=self.anchor_order,
                                target_role='admin', created_by=request.user)


def _approval_for_partial(request, amount, remaining, *, visit, anchor_order=None):
    if amount >= remaining:
        return None
    manager = request.user if can_approve_partial_payment(request.user) else None
    if manager is None:
        username = request.POST.get('manager_username', '').strip()
        password = request.POST.get('manager_password', '')
        candidate = authenticate(request, username=username, password=password)
        if candidate and can_approve_partial_payment(candidate):
            manager = candidate
    if manager is None:
        raise PartialApprovalRequired(visit, amount, anchor_order)
    return manager


def _visit_detail_context(request, visit, *, recovery=None):
    orders = list(active_visit_orders(visit))
    for order in orders:
        order.consumption_request_key = str(uuid.uuid4())
    financial = visit_financials(visit, orders=orders)
    active_sessions = list(
        visit.internet_sessions.select_related('package', 'entitlement')
        .filter(status=InternetSession.Status.ACTIVE)
        .order_by('start_time', 'pk')
    )
    payments = [
        payment
        for order in orders
        for payment in order.payments.all()
        if payment.is_active and not payment.is_reversed and payment.method != Payment.Method.UNPAID
    ]
    recovery = recovery or {}
    form_values = {'currency_amount': financial['remaining'], 'method': Payment.Method.CASH}
    settlement_values = {'method': Payment.Method.CASH}
    if recovery.get('action') == 'payment':
        form_values = recovery['values']
    elif recovery.get('action') == 'settle_close':
        settlement_values = recovery['values']
    return {
        'form_values': form_values, 'settlement_values': settlement_values,
        'payment_recovery': bool(recovery),
        'visit': visit,
        **financial,
        'payments': payments,
        'can_reverse_payments': is_finance_user(request.user),
        # Fresh form tokens distinguish separate, legitimate same-value collections.
        # Retries of the original form retain their token for exactly-once posting.
        'collection_request_key': recovery.get('key') if recovery.get('action') == 'payment' else f'visit-pay:{visit.pk}:{uuid.uuid4().hex}',
        'settlement_request_key': recovery.get('key') if recovery.get('action') == 'settle_close' else f'visit-settle:{visit.pk}:{uuid.uuid4().hex}',
        'active_internet_sessions': active_sessions,
        'active_metered_sessions': [session for session in active_sessions if not session.entitlement_id],
        'methods': COLLECTIBLE_PAYMENT_METHODS,
        'payment_amount_default': financial['remaining'],
        'currency_component': CurrencyEntryFormService(request, operation='payment').context,
        'can_manage_partial': can_approve_partial_payment(request.user),
    }


def _payment_panel_response(request, public_code, *, recovery=None):
    visit = get_object_or_404(_visit_queryset(), public_code=public_code)
    return render(request, 'staff/_visit_payment_panel.html', _visit_detail_context(request, visit, recovery=recovery))


@require_staff_capability('cashier')
def staff_cashier(request):
    query = request.GET.get('q', '').strip()
    normalized = query.lstrip('#')
    if query and normalized.isdigit():
        order = Order.objects.select_related('visit').filter(pk=int(normalized)).first()
        if order:
            if order.visit_id:
                return redirect('staff_cashier_order', public_code=order.visit.public_code)
            return redirect('staff_cashier_order', public_code=order.public_code)
        visit = customer_visit_queryset(HubVisit.objects.filter(pk=int(normalized))).first()
        if visit:
            return redirect('staff_cashier_order', public_code=visit.public_code)

    visits = _visit_queryset().filter(status=HubVisit.Status.OPEN)
    if query:
        visits = visits.filter(
            Q(table__name_ar__icontains=query)
            | Q(table__room__name_ar__icontains=query)
            | Q(member__name_ar__icontains=query)
        )
    visit_rows = [_visit_row(visit) for visit in visits.order_by('-last_activity_at')[:100]]
    return render(
        request,
        'staff/cashier.html',
        {
            'visit_rows': visit_rows,
            'standalone_rows': _standalone_rows(query),
            'query': query,
            'page_setting': get_page_setting('staff_cashier', 'الكاشير', 'Cashier'),
        },
    )


@require_staff_capability('cashier')
@require_GET
def staff_cashier_visit(request, public_code):
    visit = get_object_or_404(_visit_queryset(), public_code=public_code)
    context = _visit_detail_context(request, visit)
    if request.GET.get('panel') == 'payment':
        return render(request, 'staff/_visit_payment_panel.html', context)
    return render(request, 'staff/cashier_visit.html', context)


def _payment_fingerprint(request):
    # Credentials and CSRF tokens are deliberately excluded from durable metadata.
    values = {name: request.POST.get(name, '') for name in (
        'currency_currency', 'currency_acknowledged', 'currency_allow_stale_rate', 'method', 'notes')}
    values['amount'] = request.POST.get('currency_amount') or request.POST.get('amount_syp', '')
    return hashlib.sha256(repr(sorted(values.items())).encode()).hexdigest()


def _payment_failure_response(request, public_code, key, action):
    values = {name: request.POST.get(name, '') for name in (
        'currency_currency', 'currency_acknowledged', 'currency_allow_stale_rate', 'method', 'notes')}
    values['currency_amount'] = request.POST.get('currency_amount') or request.POST.get('amount_syp', '')
    recovery = {'key': key, 'action': action, 'values': values}
    if request.GET.get('panel') == 'payment':
        return _payment_panel_response(request, public_code, recovery=recovery)
    visit = get_object_or_404(_visit_queryset(), public_code=public_code)
    return render(request, 'staff/cashier_visit.html', _visit_detail_context(request, visit, recovery=recovery))


@require_staff_capability('cashier')
@require_POST
def staff_cashier_visit_pay(request, public_code):
    visit = get_object_or_404(HubVisit, public_code=public_code)
    posting_key = _digest_for_request(request, f'visit-cashier:{visit.pk}')
    fingerprint = _payment_fingerprint(request)
    try:
        with transaction.atomic():
            visit = HubVisit.objects.select_for_update().get(pk=visit.pk)
            receipt = visit_payment_receipt(visit, posting_key)
            if receipt:
                if (receipt.actor_id != request.user.pk or
                        receipt.request_metadata.get('cashier_payment_fingerprint', fingerprint) != fingerprint):
                    raise ValidationError('تغيّرت تفاصيل طلب الدفع أو الموظف. حدّث صفحة الحساب قبل المتابعة.')
                result_message = 'هذه الدفعة مسجلة بالفعل. لم تُنشأ دفعة أو حركة صندوق إضافية.'
            else:
                if visit.status != HubVisit.Status.OPEN:
                    raise ValidationError('هذه الجلسة مغلقة ولا تقبل دفعات جديدة.')
                current = visit_financials(visit)
                remaining = current['remaining']
                currency_service = CurrencyEntryFormService(request, operation='payment')
                currency_entry = currency_service.clean(request.POST.get('amount_syp'))
                amount = currency_entry.base_amount
                if amount <= 0:
                    raise ValidationError('المبلغ يجب أن يكون أكبر من صفر.')
                if amount > remaining:
                    raise ValidationError('المبلغ لا يجوز أن يتجاوز رصيد الجلسة المتبقي.')
                anchor_order = next((order for order in current['orders'] if order.remaining_syp > 0), None)
                approver = _approval_for_partial(request, amount, remaining, visit=visit, anchor_order=anchor_order)
                method = request.POST.get('method', '')
                if method not in dict(COLLECTIBLE_PAYMENT_METHODS):
                    raise ValidationError('اختر طريقة دفع صالحة.')
                context = PostingContext(actor=request.user, approver=approver,
                    business_date=timezone.localdate(), idempotency_key=posting_key, channel='cashier',
                    request_metadata={'path': request.path, 'visit_id': visit.pk,
                                      'cashier_payment_fingerprint': fingerprint})
                settled_visit, _allocations = allocate_visit_payment(
                    visit, context, amount, method, request.POST.get('notes', '').strip())
                currency_service.snapshot(settled_visit, currency_entry,
                    'visit_payment_' + hashlib.sha256(posting_key.encode()).hexdigest())
                if approver:
                    ActivityLog.objects.create(actor=request.user, action='visit_partial_payment_approved', details={
                        'visit_id': visit.pk, 'amount_syp': str(amount), 'remaining_before_payment': remaining,
                        'approving_manager_username': approver.username, 'cashier_username': request.user.username})
                result_message = f'تم تسجيل دفعة على الجلسة بقيمة {amount} ل.س.'
        messages.success(request, result_message)
    except PartialApprovalRequired as error:
        try:
            error.notify(request)
        except Exception:
            logging.getLogger(__name__).exception('Partial approval notification failed')
        messages.error(request, str(error))
        return _payment_failure_response(request, public_code, posting_key, 'payment')
    except (ValidationError, PermissionDenied) as error:
        messages.error(request, ' '.join(getattr(error, 'messages', [str(error)])))
        return _payment_failure_response(request, public_code, posting_key, 'payment')
    except Exception:
        logging.getLogger(__name__).exception('Visit payment save failed')
        messages.error(request, 'تعذر تأكيد حفظ الدفعة. راجع الرصيد وسجل الدفعات قبل إعادة المحاولة من النموذج نفسه.')
        return _payment_failure_response(request, public_code, posting_key, 'payment')
    if request.GET.get('panel') == 'payment':
        return _payment_panel_response(request, public_code)
    return redirect('staff_cashier_order', public_code=visit.public_code)


@require_staff_capability('cashier')
@require_POST
def staff_cashier_visit_settle(request, public_code):
    visit = get_object_or_404(HubVisit.objects.select_related('table'), public_code=public_code)
    method = request.POST.get('method', '')
    notes = request.POST.get('notes', '').strip()
    posting_key = _digest_for_request(request, f'visit-settle:{visit.pk}')

    try:
        with transaction.atomic():
            # Match reservation check-in/visit-close lock ordering.
            Reservation.objects.select_for_update().filter(visit_id=visit.pk).first()
            visit = HubVisit.objects.select_for_update().get(pk=visit.pk)
            if visit.status != HubVisit.Status.OPEN:
                messages.info(request, 'الجلسة مغلقة بالفعل.')
                if request.GET.get('panel') == 'payment':
                    return _payment_panel_response(request, public_code)
                return redirect('staff_cashier_order', public_code=visit.public_code)

            # The displayed due excludes any still-running metered sessions.
            recorded_due_before_finalization = visit_financials(visit)['remaining']
            now = timezone.now()
            sessions = list(
                InternetSession.objects.select_for_update()
                .filter(visit=visit, status=InternetSession.Status.ACTIVE)
                .order_by('pk')
            )
            for session in sessions:
                if session.entitlement_id:
                    end_usage_session(session, actor=request.user, at=now)
                else:
                    finalize_visit_metered_session(session, actor=request.user, at=now)

            financial = visit_financials(visit)
            remaining = financial['remaining']
            metered_added_due = max(remaining - recorded_due_before_finalization, 0)
            if remaining > 0:
                if method not in dict(COLLECTIBLE_PAYMENT_METHODS):
                    raise ValidationError('اختر طريقة الدفع قبل تسديد وإغلاق الجلسة.')
                context = PostingContext(
                    actor=request.user,
                    business_date=timezone.localdate(),
                    idempotency_key=posting_key,
                    channel='cashier',
                    request_metadata={
                        'path': request.path,
                        'visit_id': visit.pk,
                        'settle_and_close': True,
                    },
                )
                allocate_visit_payment(visit, context, remaining, method, notes)

            if visit_financials(visit)['remaining']:
                raise ValidationError('تعذر إغلاق الجلسة لأن رصيداً متبقياً ما زال قائماً.')

            visit.status = HubVisit.Status.CLOSED
            visit.closed_at = now
            visit.last_activity_at = now
            visit.save(update_fields=['status', 'closed_at', 'last_activity_at', 'updated_at'])
            visit.browser_credentials.filter(revoked_at__isnull=True).update(revoked_at=now)
            complete_reservation_for_visit(visit.pk, actor=request.user)
            ActivityLog.objects.create(
                actor=request.user,
                action='visit.settled_and_closed',
                details={
                    'visit_id': visit.pk,
                    'amount_syp': str(remaining),
                    'recorded_due_before_finalization_syp': str(recorded_due_before_finalization),
                    'new_metered_due_syp': str(metered_added_due),
                    'method': method if remaining else '',
                    'posting_key': posting_key,
                    'ended_internet_sessions': [session.pk for session in sessions],
                },
            )
        if remaining == 0:
            messages.success(request, 'أُغلقت الجلسة دون تحصيل دفعة جديدة.')
        elif metered_added_due:
            messages.success(request, f'تم تحصيل {remaining} ل.س وإغلاق الحساب. يتضمن المبلغ {metered_added_due} ل.س من رسوم الإنترنت التي حُسبت عند الإغلاق.')
        else:
            messages.success(request, f'تم تحصيل {remaining} ل.س وإغلاق الحساب.')
    except (ValidationError, PermissionDenied) as error:
        messages.error(request, ' '.join(getattr(error, 'messages', [str(error)])))
        return _payment_failure_response(request, public_code, posting_key, 'settle_close')
    except Exception:
        logging.getLogger(__name__).exception('Visit settlement save failed')
        messages.error(request, 'تعذر تأكيد تسديد وإغلاق الحساب. راجع الحالة وسجل الدفعات قبل إعادة المحاولة من النموذج نفسه.')
        return _payment_failure_response(request, public_code, posting_key, 'settle_close')

    if request.GET.get('panel') == 'payment':
        return _payment_panel_response(request, public_code)
    return redirect('staff_cashier_order', public_code=visit.public_code)


def _receipt_context(request, visit, template_kind):
    visit = get_object_or_404(_visit_queryset(), pk=visit.pk)
    context = _visit_detail_context(request, visit)
    system_settings = get_system_settings()
    context.update({
        'business_name': (
            (system_settings.receipt_business_name or system_settings.public_brand_title)
            if system_settings else 'مشاريب'
        ),
        'receipt_footer_text': (
            (system_settings.receipt_footer_text if system_settings else '') or 'شكراً لزيارتكم'
        ),
        'thermal_width': getattr(system_settings, 'thermal_receipt_width_mm', 80) or 80,
        'template_kind': template_kind,
    })
    return context


@require_staff_capability('cashier')
@require_GET
def staff_cashier_visit_receipt(request, public_code):
    visit = get_object_or_404(HubVisit, public_code=public_code)
    ActivityLog.objects.create(
        actor=request.user,
        action='visit_receipt_print_viewed',
        details={'visit_id': visit.pk, 'template': 'a4'},
    )
    return render(request, 'staff/prints/visit_receipt.html', _receipt_context(request, visit, 'a4'))


@require_staff_capability('cashier')
@require_GET
def staff_cashier_visit_receipt_thermal(request, public_code):
    visit = get_object_or_404(HubVisit, public_code=public_code)
    ActivityLog.objects.create(
        actor=request.user,
        action='visit_receipt_print_viewed',
        details={'visit_id': visit.pk, 'template': 'thermal'},
    )
    return render(request, 'staff/prints/visit_receipt_thermal.html', _receipt_context(request, visit, 'thermal'))
