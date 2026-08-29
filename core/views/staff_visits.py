from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.permissions import require_staff_capability, user_has_capability
from core.models import ActivityLog, HubVisit, InternetSession, Member, Order, TableArea
from core.services.internet_access import end_usage_session
from core.services.visit_internet import finalize_visit_metered_session
from reservations.models import Reservation
from reservations.services import complete_reservation_for_visit


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
    internet_sessions = visit.internet_sessions.select_related(
        'package', 'entitlement', 'browser_binding__credential'
    ).order_by('-start_time')
    return {
        'visit': visit,
        'tables': TableArea.objects.select_related('room'),
        'members': Member.objects.order_by('name_ar')[:200],
        'internet_entitlements': visit.internet_entitlements.select_related('package', 'order'),
        'internet_sessions': internet_sessions,
        'active_internet_sessions': internet_sessions.filter(status=InternetSession.Status.ACTIVE),
        'unpaid_order': unpaid_order,
        'visit_caps': {
            'pos': user_has_capability(request.user, 'pos'),
            'cashier': user_has_capability(request.user, 'cashier'),
            'internet_billing': user_has_capability(request.user, 'internet_billing'),
            'order_edit': user_has_capability(request.user, 'order_edit'),
        },
    }


def _workspace_redirect(visit):
    return redirect(f'/staff/?visit={visit.public_code}')


def _render_visit_panel(request, visit, panel):
    context = _visit_context(request, visit)
    if panel == 'internet':
        return render(request, 'staff/_visit_internet_panel.html', context)
    if panel == '1':
        return render(request, 'staff/_visit_panel.html', context)
    return render(request, 'staff/visit_detail.html', context)


@require_staff_capability('orders')
@require_http_methods(['GET', 'POST'])
def staff_visits(request):
    if request.method == 'POST':
        with transaction.atomic():
            table = None
            if request.POST.get('table'):
                table = TableArea.objects.select_for_update().filter(pk=request.POST.get('table')).first()
                if table is None:
                    messages.error(request, 'الطاولة المختارة غير صالحة.')
                    return redirect('staff_visits')
                occupied = HubVisit.objects.select_for_update().filter(
                    table=table,
                    status=HubVisit.Status.OPEN,
                ).first()
                if occupied:
                    messages.info(request, f'الطاولة لديها جلسة مفتوحة بالفعل: {occupied}.')
                    if request.POST.get('next') == 'workspace':
                        return _workspace_redirect(occupied)
                    return redirect('staff_visit_detail', public_code=occupied.public_code)

            member = None
            if request.POST.get('member'):
                member = Member.objects.select_for_update().filter(pk=request.POST.get('member')).first()
                if member is None:
                    messages.error(request, 'العضو المختار غير صالح.')
                    return redirect('staff_visits')
                existing_member_visit = HubVisit.objects.select_for_update().filter(
                    member=member,
                    status=HubVisit.Status.OPEN,
                ).first()
                if existing_member_visit:
                    messages.info(request, f'لدى العضو جلسة مفتوحة بالفعل: {existing_member_visit}.')
                    if request.POST.get('next') == 'workspace':
                        return _workspace_redirect(existing_member_visit)
                    return redirect('staff_visit_detail', public_code=existing_member_visit.public_code)

            visit = HubVisit.objects.create(
                table=table,
                member=member,
                notes=request.POST.get('notes', '').strip(),
                created_by=request.user,
            )
            _log(request, 'visit.created', visit, source='staff')
        messages.success(request, f'تم إنشاء جلسة {visit.display_number}.')
        if request.POST.get('next') == 'workspace':
            return _workspace_redirect(visit)
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
    visit = get_object_or_404(
        HubVisit.objects.select_related('table', 'table__room', 'member').prefetch_related(
            'orders__items', 'orders__discounts', 'orders__payments', 'browser_credentials'
        ),
        public_code=public_code,
    )
    panel = request.GET.get('panel', '')
    if request.method == 'POST':
        action = request.POST.get('action')
        with transaction.atomic():
            # Reservation check-in locks Reservation -> HubVisit. Keep that same
            # order for closure so a repeated check-in cannot deadlock closure.
            if action == 'close':
                Reservation.objects.select_for_update().filter(visit_id=visit.pk).first()
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
            elif action == 'internet_stop':
                if not user_has_capability(request.user, 'internet_billing'):
                    raise PermissionDenied('لا تملك صلاحية إدارة الإنترنت.')
                session = get_object_or_404(
                    InternetSession.objects.select_for_update(),
                    pk=request.POST.get('session_id'),
                    visit=visit,
                    status=InternetSession.Status.ACTIVE,
                )
                now = timezone.now()
                if session.entitlement_id:
                    ended = end_usage_session(session, actor=request.user, at=now)
                else:
                    ended = finalize_visit_metered_session(session, actor=request.user, at=now)
                visit.last_activity_at = now
                visit.save(update_fields=['last_activity_at', 'updated_at'])
                _log(
                    request,
                    'visit.internet_session_ended',
                    visit,
                    session_id=ended.pk,
                    entitlement_id=ended.entitlement_id,
                    source='staff_operations',
                )
                messages.success(request, 'تم إيقاف جلسة الإنترنت وتحديث الحساب.')
            elif action == 'close':
                now = timezone.now()
                active_sessions = list(InternetSession.objects.select_for_update().filter(
                    visit=visit, status=InternetSession.Status.ACTIVE))
                for session in active_sessions:
                    if session.entitlement_id:
                        ended = end_usage_session(session, actor=request.user, at=now)
                    else:
                        ended = finalize_visit_metered_session(session, actor=request.user, at=now)
                    _log(request, 'visit.internet_session_ended', visit,
                         session_id=ended.pk, entitlement_id=ended.entitlement_id)

                # A metered session can create a new billed order while being
                # stopped above. Re-check the visit balance only after all active
                # Internet sessions have been finalized.
                if visit.remaining_syp:
                    messages.error(request, 'تم إيقاف الإنترنت إن كان فعالاً. لا يمكن إغلاق الجلسة قبل تسديد الرصيد المتبقي.')
                    if panel in {'1', 'internet'}:
                        visit = get_object_or_404(HubVisit, pk=visit.pk)
                        return _render_visit_panel(request, visit, panel)
                    return redirect('staff_visit_detail', public_code=visit.public_code)

                visit.status = HubVisit.Status.CLOSED; visit.closed_at = now; visit.last_activity_at = now
                visit.save(update_fields=['status', 'closed_at', 'last_activity_at', 'updated_at'])
                visit.browser_credentials.filter(revoked_at__isnull=True).update(revoked_at=now)
                complete_reservation_for_visit(visit.pk, actor=request.user)
                _log(request, 'visit.closed', visit)
            else:
                messages.error(request, 'الإجراء غير صالح.')
                if panel in {'1', 'internet'}:
                    return _render_visit_panel(request, visit, panel)
                return redirect('staff_visit_detail', public_code=visit.public_code)
        messages.success(request, 'تم تحديث الجلسة.')
        if panel in {'1', 'internet'}:
            visit = get_object_or_404(
                HubVisit.objects.select_related('table', 'table__room', 'member').prefetch_related(
                    'orders__items', 'orders__discounts', 'orders__payments', 'browser_credentials'
                ),
                pk=visit.pk,
            )
            return _render_visit_panel(request, visit, panel)
        return redirect('staff_visit_detail', public_code=visit.public_code)

    return _render_visit_panel(request, visit, panel)
