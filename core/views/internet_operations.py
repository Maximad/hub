from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from accounts.permissions import require_staff_capability
from core.models import InternetNetworkOperation, InternetSession
from core.services.internet_operations import (
    requeue_failed_network_operation,
    run_readonly_mikrotik_healthcheck,
)
from core.services.internet_readiness import (
    internet_readiness_report,
    mikrotik_enablement_preflight,
    worker_is_fresh,
)
from internet.models import InternetSessionNetworkOperation


@require_staff_capability('settings')
def internet_operations(request):
    report = internet_readiness_report()
    preflight = mikrotik_enablement_preflight()
    state = preflight['state']

    entitlement_counts = {
        status: InternetNetworkOperation.objects.filter(status=status).count()
        for status, _ in InternetNetworkOperation.Status.choices
    }
    session_counts = {
        status: InternetSessionNetworkOperation.objects.filter(status=status).count()
        for status, _ in InternetSessionNetworkOperation.Status.choices
    }

    entitlement_operations = list(
        InternetNetworkOperation.objects.select_related(
            'entitlement', 'entitlement__member', 'entitlement__package',
        ).order_by('-updated_at')[:30]
    )
    session_operations = list(
        InternetSessionNetworkOperation.objects.select_related(
            'session', 'session__member', 'session__visit',
        ).order_by('-updated_at')[:30]
    )

    active_session_rows = []
    sessions = (
        InternetSession.objects.filter(status=InternetSession.Status.ACTIVE)
        .select_related('member', 'visit', 'network_state')
        .order_by('-start_time', '-pk')[:30]
    )
    for session in sessions:
        try:
            network_state = session.network_state
        except ObjectDoesNotExist:
            network_state = None
        active_session_rows.append({
            'session': session,
            'network_state': network_state,
        })

    context = {
        'readiness': report,
        'preflight': preflight,
        'operations_state': state,
        'worker_fresh': worker_is_fresh(state),
        'entitlement_counts': entitlement_counts,
        'session_counts': session_counts,
        'entitlement_operations': entitlement_operations,
        'session_operations': session_operations,
        'active_session_rows': active_session_rows,
    }
    return render(request, 'staff/internet_operations.html', context)


@require_POST
@require_staff_capability('settings')
def internet_operations_retry(request, kind, operation_id):
    try:
        requeue_failed_network_operation(
            kind=kind,
            operation_id=operation_id,
            actor=request.user,
        )
    except ValidationError as exc:
        messages.error(request, next(iter(exc.messages), 'تعذر إعادة محاولة عملية الشبكة.'))
    else:
        messages.success(request, 'أعيدت العملية إلى قائمة الانتظار. سيلتقطها عامل الإنترنت تلقائياً.')
    return redirect('staff_internet_operations')


@require_POST
@require_staff_capability('settings')
def internet_operations_healthcheck(request):
    ok, message = run_readonly_mikrotik_healthcheck(actor=request.user)
    if ok:
        messages.success(request, message)
    else:
        messages.error(request, message)
    return redirect('staff_internet_operations')
