from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.permissions import require_staff_capability, user_has_capability
from core.models import ActivityLog, HubVisit, InternetSession, Member, Order, TableArea
from core.services.internet_access import end_usage_session


def _log(request, action, visit, **details):
    ActivityLog.objects.create(actor=request.user, action=action, details={'visit_id': visit.pk, **details})


def _visit_context(request, visit):
    orders = list(visit.orders.all())
    unpaid_order = next(
        (
            order
            for order in orders
            if order.status != Order.Status.CANCELLED and order.remaining_syp > 0
        ),
        None,
    )
    return {
        'visit': visit,
        'tables': TableArea.objects.select_related('room'),
        'members': Member.objects.order_by('name_ar')[:200],
        'internet_entitlements': visit.internet_entitlements.select_related('package', 'order'),
        'internet_sessions': visit.internet_sessions.select_related('package', 'entitlement').order_by('-start_time'),
        'unpaid_order': unpaid_order,
        'visit_caps': {
            'pos': user_has_capability(request.user, 'pos'),
            'cashier': user_has_capability(request.user, 'cashier'),
            'internet_billing': user_has_capability(request.user, 'internet_billing'),
            'order_edit': user_has_capability(request.user, 'order_edit'),
        },
    }


@require_staff_capability('orders')
@require_http_methods(['GET', 'POST'])
def staff_visits(request):
    if request.method == 'POST':
        table = TableArea.objects.filter(pk=request.POST.get('table')).first() if request.POST.get('table') else None
        member = Member.objects.filter(pk=request.POST.get('member')).first() if request.POST.get('member') else None
        visit = HubVisit.objects.create(table=table, member=member, notes=request.POST.get('notes', '').strip(), created_by=request.user)
        _log(request, 'visit.created', visit, source='staff')
        messages.success(request, f'تم إنشاء جلسة {visit.display_number}.')
        return redirect('staff_visit_detail', public_code=visit.public_code)
    visits = HubVisit.objects.select_related('table', 'table__room', 'member').prefetch_related('orders__items', 'orders__discounts', 'orders__payments')
    status = request.GET.get('status', 'open')
    if status in HubVisit.Status.values:
        visits = visits.filter(status=status)
    if request.GET.get('table'):
        visits = visits.filter(table_id=request.GET['table'])
    if request.GET.get('member'):
        visits = visits.filter(member_id=request.GET['member'])
    return render(request, 'staff/visits.html', {'visits': visits, 'status_filter': status, 'tables': TableArea.objects.select_related('room'), 'members': Member.objects.order_by('name_ar')[:200]})


@require_staff_capability('orders')
@require_http_methods(['GET', 'POST'])
def staff_visit_detail(request, public_code):
    visit = get_object_or_404(HubVisit.objects.select_related('table', 'table__room', 'member').prefetch_related('orders__items', 'orders__discounts', 'orders__payments', 'browser_credentials'), public_code=public_code)
    if request.method == 'POST':
        action = request.POST.get('action')
        with transaction.atomic():
            visit = HubVisit.objects.select_for_update().get(pk=visit.pk)
            if action == 'attach_order':
                order = get_object_or_404(Order, public_code=request.POST.get('order_code'))
                old_visit_id = order.visit_id
                order.visit = visit
                order.save(update_fields=['visit', 'updated_at'])
                visit.last_activity_at = timezone.now(); visit.save(update_fields=['last_activity_at', 'updated_at'])
                _log(request, 'visit.order_attached', visit, order_id=order.pk, old_visit_id=old_visit_id)
            elif action == 'detach_order':
                order = get_object_or_404(Order, pk=request.POST.get('order_id'), visit=visit)
                order.visit = None; order.save(update_fields=['visit', 'updated_at'])
                _log(request, 'visit.order_attached', visit, order_id=order.pk, detached=True)
            elif action == 'change_table':
                old_id = visit.table_id
                visit.table = TableArea.objects.filter(pk=request.POST.get('table')).first() if request.POST.get('table') else None
                visit.last_activity_at = timezone.now(); visit.save(update_fields=['table', 'last_activity_at', 'updated_at'])
                _log(request, 'visit.table_changed', visit, old_table_id=old_id, new_table_id=visit.table_id)
            elif action in {'attach_member', 'detach_member'}:
                old_id = visit.member_id
                visit.member = Member.objects.filter(pk=request.POST.get('member')).first() if action == 'attach_member' else None
                visit.last_activity_at = timezone.now(); visit.save(update_fields=['member', 'last_activity_at', 'updated_at'])
                _log(request, 'visit.member_attached' if visit.member_id else 'visit.member_detached', visit, old_member_id=old_id, new_member_id=visit.member_id)
            elif action == 'close':
                if visit.remaining_syp:
                    messages.error(request, 'لا يمكن إغلاق الجلسة قبل تسديد الرصيد المتبقي.')
                    return redirect('staff_visit_detail', public_code=visit.public_code)
                now = timezone.now()
                active_sessions = list(InternetSession.objects.select_for_update().filter(
                    visit=visit, status=InternetSession.Status.ACTIVE))
                for session in active_sessions:
                    ended = end_usage_session(session, actor=request.user, at=now)
                    _log(request, 'visit.internet_session_ended', visit,
                         session_id=ended.pk, entitlement_id=ended.entitlement_id)
                visit.status = HubVisit.Status.CLOSED; visit.closed_at = now; visit.last_activity_at = now
                visit.save(update_fields=['status', 'closed_at', 'last_activity_at', 'updated_at'])
                visit.browser_credentials.filter(revoked_at__isnull=True).update(revoked_at=now)
                _log(request, 'visit.closed', visit)
            else:
                messages.error(request, 'الإجراء غير صالح.')
                return redirect('staff_visit_detail', public_code=visit.public_code)
        messages.success(request, 'تم تحديث الجلسة.')
        return redirect('staff_visit_detail', public_code=visit.public_code)

    context = _visit_context(request, visit)
    if request.GET.get('panel') == '1':
        return render(request, 'staff/_visit_panel.html', context)
    return render(request, 'staff/visit_detail.html', context)
