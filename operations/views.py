from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .insights import business_day_metrics, detect_anomalies, send_owner_digest
from .models import BusinessDay, BusinessDayChecklistItem, BusinessDayException, HandoverNote
from .opening import (
    acknowledge_handover,
    active_staff_queryset,
    create_handover_note,
    resolve_handover,
    send_daily_code_reminders,
    set_staff_roster,
    unresolved_handover_notes,
    update_checklist_item,
)
from .services import (
    build_reconciliation,
    carry_forward_exception,
    close_open_internet_sessions,
    close_safe_visits,
    current_business_date,
    finalize_business_day,
    issue_daily_code,
    open_business_day,
    reopen_business_day,
    reveal_daily_code,
    start_closing,
    sync_exceptions,
    verify_daily_code,
)


MANAGER_ROLES = {'admin', 'cashier'}


def _is_staff_user(user):
    if not user.is_authenticated or not user.is_active:
        return False
    provider_role = getattr(get_user_model().Role, 'INTERNET_PROVIDER', 'internet_provider')
    return user.is_superuser or getattr(user, 'role', '') != provider_role


def _can_manage(user):
    return bool(user.is_superuser or getattr(user, 'role', '') in MANAGER_ROLES)


def _can_reopen(user):
    return bool(user.is_superuser or getattr(user, 'role', '') == 'admin')


def _validation_text(exc):
    return ' '.join(getattr(exc, 'messages', [str(exc)]))


def _current_day():
    active = BusinessDay.objects.filter(
        status__in=[BusinessDay.Status.OPEN, BusinessDay.Status.CLOSING, BusinessDay.Status.REOPENED]
    ).order_by('-business_date').first()
    if active:
        return active
    return BusinessDay.objects.filter(business_date=current_business_date()).first()


@login_required
def staff_business_day(request):
    if not _is_staff_user(request.user):
        raise PermissionDenied

    day = _current_day()
    revealed_code = None

    if request.method == 'POST':
        action = (request.POST.get('business_day_action') or '').strip()
        try:
            if action == 'open':
                if not _can_manage(request.user):
                    raise PermissionDenied
                day = open_business_day(actor=request.user)
                selected_staff = request.POST.getlist('staff_user_ids') or [str(request.user.pk)]
                set_staff_roster(day, actor=request.user, user_ids=selected_staff)
                messages.success(request, f'تم فتح يوم العمل {day.business_date} وتثبيت فريق اليوم وإصدار الرمز.')
                return redirect('staff_close_day')

            if not day:
                raise ValidationError('لا يوجد يوم تشغيلي مفتوح.')

            if action == 'reveal_code':
                revealed_code = reveal_daily_code(day, user=request.user)
                messages.info(request, 'تم تسجيل عرض رمز اليوم في سجل النشاط.')

            elif action == 'verify_code':
                if verify_daily_code(day, request.POST.get('daily_code'), user=request.user):
                    messages.success(request, 'رمز اليوم صحيح وتم تسجيل الاستخدام.')
                else:
                    messages.error(request, 'رمز اليوم غير صحيح أو غير صالح.')

            elif action == 'rotate_code':
                if not _can_manage(request.user):
                    raise PermissionDenied
                issue_daily_code(day, actor=request.user)
                messages.success(request, 'تم تغيير رمز اليوم وإرسال إشعار جديد للفريق.')
                return redirect('staff_close_day')

            elif action == 'send_code_reminders':
                if not _can_manage(request.user):
                    raise PermissionDenied
                count = send_daily_code_reminders(day, actor=request.user, min_interval_minutes=1)
                messages.info(request, f'تم إرسال تذكير إلى {count} من فريق اليوم ممن لم يؤكدوا الرمز.')
                return redirect('staff_close_day')

            elif action == 'staff_roster':
                if not _can_manage(request.user):
                    raise PermissionDenied
                set_staff_roster(day, actor=request.user, user_ids=request.POST.getlist('staff_user_ids'))
                messages.success(request, 'تم تحديث فريق العمل لهذا اليوم.')
                return redirect('staff_close_day')

            elif action == 'checklist':
                if not _can_manage(request.user):
                    raise PermissionDenied
                item = get_object_or_404(
                    BusinessDayChecklistItem,
                    pk=request.POST.get('checklist_item_id'),
                    business_day=day,
                )
                update_checklist_item(
                    item,
                    actor=request.user,
                    status=(request.POST.get('checklist_status') or '').strip(),
                    note=request.POST.get('checklist_note', ''),
                )
                messages.success(request, 'تم تحديث بند الافتتاح.')
                return redirect('staff_close_day')

            elif action == 'handover_add':
                assigned_to = None
                assigned_to_id = (request.POST.get('assigned_to') or '').strip()
                if assigned_to_id:
                    assigned_to = active_staff_queryset().filter(pk=assigned_to_id).first()
                    if assigned_to is None:
                        raise ValidationError('الموظف المعيّن غير صالح.')
                create_handover_note(
                    day,
                    actor=request.user,
                    message=request.POST.get('handover_message', ''),
                    priority=(request.POST.get('handover_priority') or HandoverNote.Priority.NORMAL),
                    assigned_to=assigned_to,
                )
                messages.success(request, 'تمت إضافة ملاحظة التسليم وإشعار المعنيين.')
                return redirect('staff_close_day')

            elif action == 'handover_ack':
                note = get_object_or_404(HandoverNote, pk=request.POST.get('handover_id'))
                acknowledge_handover(note, actor=request.user)
                messages.success(request, 'تم تسجيل الاطلاع على ملاحظة التسليم.')
                return redirect('staff_close_day')

            elif action == 'handover_resolve':
                if not _can_manage(request.user):
                    raise PermissionDenied
                note = get_object_or_404(HandoverNote, pk=request.POST.get('handover_id'))
                resolve_handover(note, actor=request.user)
                messages.success(request, 'تم إغلاق ملاحظة التسليم.')
                return redirect('staff_close_day')

            elif action == 'start_closing':
                if not _can_manage(request.user):
                    raise PermissionDenied
                day, report = start_closing(day, actor=request.user)
                messages.info(
                    request,
                    f'بدأ وضع الإغلاق: {report["blocker_count"]} مانع و{report["warning_count"]} تحذير.',
                )
                return redirect('staff_close_day')

            elif action == 'refresh':
                if not _can_manage(request.user):
                    raise PermissionDenied
                report = sync_exceptions(day, actor=request.user)
                messages.info(
                    request,
                    f'تم تحديث الفحص: {report["blocker_count"]} مانع و{report["warning_count"]} تحذير.',
                )
                return redirect('staff_close_day')

            elif action == 'close_sessions':
                if not _can_manage(request.user):
                    raise PermissionDenied
                result = close_open_internet_sessions(day, actor=request.user)
                if result['failures']:
                    messages.warning(
                        request,
                        f'تم إنهاء {result["closed"]} جلسة، وتعذر إنهاء {len(result["failures"])} جلسة.',
                    )
                else:
                    messages.success(request, f'تم إنهاء {result["closed"]} جلسة إنترنت مفتوحة.')
                return redirect('staff_close_day')

            elif action == 'close_safe_visits':
                if not _can_manage(request.user):
                    raise PermissionDenied
                result = close_safe_visits(day, actor=request.user)
                messages.success(
                    request,
                    f'تم إغلاق {result["closed"]} زيارة آمنة، وتُركت {result["skipped"]} زيارة تحتاج متابعة.',
                )
                return redirect('staff_close_day')

            elif action == 'carry_forward':
                if not _can_manage(request.user):
                    raise PermissionDenied
                exception = get_object_or_404(
                    BusinessDayException,
                    pk=request.POST.get('exception_id'),
                    business_day=day,
                )
                carry_forward_exception(
                    exception,
                    actor=request.user,
                    note=request.POST.get('carry_note', ''),
                )
                messages.success(request, 'تم ترحيل التحذير مع تسجيل السبب.')
                return redirect('staff_close_day')

            elif action == 'finalize':
                if not _can_manage(request.user):
                    raise PermissionDenied
                day = finalize_business_day(day, actor=request.user)
                send_owner_digest(day, actor=request.user)
                messages.success(request, f'تم إغلاق يوم العمل {day.business_date} وإرسال ملخص الإدارة.')
                return redirect('staff_close_day')

            elif action == 'reopen':
                if not _can_reopen(request.user):
                    raise PermissionDenied
                day = reopen_business_day(
                    day,
                    actor=request.user,
                    reason=request.POST.get('reopen_reason', ''),
                )
                messages.warning(request, f'أعيد فتح يوم {day.business_date} وتم إصدار رمز فريق جديد.')
                return redirect('staff_close_day')

        except ValidationError as exc:
            messages.error(request, _validation_text(exc))

    day = day or _current_day()
    can_manage = _can_manage(request.user)
    staff_options = active_staff_queryset() if can_manage else []
    assignments = day.staff_assignments.select_related('user').all() if day else []
    assigned_staff_ids = list(assignments.values_list('user_id', flat=True)) if day else []
    checklist_items = day.checklist_items.order_by('sort_order', 'pk') if day else []
    handover_notes = unresolved_handover_notes(day) if day else []
    report = build_reconciliation(day) if day and can_manage else None
    exceptions = (
        day.exceptions.select_related('resolved_by').order_by('-severity', 'category', 'created_at')
        if day and can_manage else []
    )
    receipts = (
        day.code_receipts.select_related('user').order_by('user__username')
        if day and can_manage else []
    )
    metrics = business_day_metrics(day) if day and can_manage else None
    anomalies = detect_anomalies(day) if day and can_manage else []
    recent_days = BusinessDay.objects.select_related('opened_by', 'closed_by').order_by('-business_date')[:14]

    return render(request, 'operations/business_day.html', {
        'business_day': day,
        'business_date_today': current_business_date(),
        'revealed_code': revealed_code,
        'can_manage_business_day': can_manage,
        'can_reopen_business_day': _can_reopen(request.user),
        'staff_options': staff_options,
        'staff_assignments': assignments,
        'assigned_staff_ids': assigned_staff_ids,
        'opening_checklist_items': checklist_items,
        'handover_notes': handover_notes,
        'reconciliation': report,
        'business_day_exceptions': exceptions,
        'code_receipts': receipts,
        'business_day_metrics': metrics,
        'business_day_anomalies': anomalies,
        'recent_business_days': recent_days,
        'now': timezone.now(),
    })
