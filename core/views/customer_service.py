from datetime import timedelta

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import ActivityLog, HubVisit, Order
from core.notifications import create_notification
from core.services.visits import resolve_visit_credential

REQUEST_THROTTLE = timedelta(minutes=2)


def _visit_label(visit):
    if visit.table_id:
        return visit.table.display_name
    return 'جلسة هَبّ'


def _recent_request(visit, action):
    return ActivityLog.objects.filter(
        action=f'customer.{action}_requested',
        created_at__gte=timezone.now() - REQUEST_THROTTLE,
        details__visit_id=visit.pk,
    ).exists()


def _log_request(visit, action, order):
    ActivityLog.objects.create(
        action=f'customer.{action}_requested',
        details={
            'visit_id': visit.pk,
            'visit_public_code': str(visit.public_code),
            'table_id': visit.table_id,
            'order_id': order.pk if order else None,
        },
    )


@require_POST
def customer_visit_request(request):
    credential = resolve_visit_credential(request)
    if not credential:
        messages.error(request, 'الجلسة غير متاحة. افتح المنيو من رمز الطاولة من جديد.')
        return redirect('menu_public')

    visit = credential.visit
    if visit.status != HubVisit.Status.OPEN:
        messages.info(request, 'هذه الجلسة مغلقة بالفعل.')
        return redirect('menu_public')

    action = (request.POST.get('action') or '').strip()
    if action not in {'bill', 'assistance'}:
        messages.error(request, 'الطلب غير معروف.')
        return redirect('current_visit')

    if _recent_request(visit, action):
        messages.info(request, 'تم إبلاغ الفريق بالفعل قبل قليل.')
        return redirect(reverse('current_visit') + '#visit-actions')

    latest_order = (
        visit.orders.exclude(status=Order.Status.CANCELLED)
        .order_by('-created_at', '-pk')
        .first()
    )
    label = _visit_label(visit)

    if action == 'bill':
        if latest_order is None or visit.remaining_syp <= 0:
            messages.info(request, 'لا يوجد مبلغ متبقٍ لطلب الحساب حالياً.')
            return redirect(reverse('current_visit') + '#visit-actions')
        create_notification(
            'payment_pending',
            f'{label} تطلب الحساب',
            f'المتبقي على الجلسة: {visit.remaining_syp} ل.س',
            order=latest_order,
            target_role='cashier',
        )
        _log_request(visit, action, latest_order)
        messages.success(request, 'تم إبلاغ الفريق بأنك تريد الحساب.')
    else:
        create_notification(
            'order_edited',
            f'مساعدة مطلوبة — {label}',
            'الزبون يطلب مساعدة من الفريق.',
            order=latest_order,
            target_role='service',
        )
        _log_request(visit, action, latest_order)
        messages.success(request, 'تم إبلاغ الفريق. سيأتي أحدهم إليك.')

    return redirect(reverse('current_visit') + '#visit-actions')
