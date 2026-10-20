from datetime import datetime, time

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import BusinessDay, BusinessDayTask, OperationalTaskTemplate, OperationalReviewCase
from .review_cases import (
    can_recheck_case, case_duty_holder, confirm_case_recheck,
    preview_case_recheck, review_presentation, run_review_sync,
)
from .reminders import reminders_enabled
from .opening import active_staff_queryset
from .services import current_business_date
from .tasks import (
    assign_task,
    create_manual_task,
    create_task_template,
    set_task_status,
    sync_business_day_tasks,
)


MANAGER_ROLES = {'admin', 'cashier'}


def _is_staff_user(user):
    if not user.is_authenticated or not user.is_active:
        return False
    provider_role = getattr(get_user_model().Role, 'INTERNET_PROVIDER', 'internet_provider')
    return user.is_superuser or getattr(user, 'role', '') != provider_role


def _can_manage(user):
    return bool(user.is_superuser or getattr(user, 'role', '') in MANAGER_ROLES)


def _current_day():
    active = BusinessDay.objects.filter(
        status__in=[BusinessDay.Status.OPEN, BusinessDay.Status.CLOSING, BusinessDay.Status.REOPENED]
    ).order_by('-business_date').first()
    if active:
        return active
    return BusinessDay.objects.filter(business_date=current_business_date()).first()


def _validation_text(exc):
    return ' '.join(getattr(exc, 'messages', [str(exc)]))


def _parse_local_datetime(raw):
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValidationError('وقت المهمة غير صالح.') from exc
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone=timezone.get_current_timezone())
    return value


def _parse_time(raw):
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return time.fromisoformat(raw)
    except ValueError as exc:
        raise ValidationError('وقت المهمة الدورية غير صالح.') from exc


def _can_update_task(user, task):
    if _can_manage(user):
        return True
    if task.assigned_to_id == user.pk:
        return True
    return bool(task.responsibility_role and task.responsibility_role == getattr(user, 'role', ''))


@login_required
def staff_operational_tasks(request):
    if not _is_staff_user(request.user):
        raise PermissionDenied

    day = _current_day()
    can_manage = _can_manage(request.user)
    recheck_preview = None

    if request.method == 'POST':
        if not day:
            messages.error(request, 'لا يوجد يوم تشغيلي مفتوح.')
            return redirect('staff_operational_tasks')
        action = (request.POST.get('task_action') or '').strip()
        try:
            if action == 'sync':
                if not can_manage:
                    raise PermissionDenied
                result = sync_business_day_tasks(day, actor=request.user)
                messages.info(
                    request,
                    f'تمت مزامنة المهام: {result["created"]} جديدة، {result["auto_waived"]} أُغلقت تلقائياً بعد زوال سببها.',
                )

            elif action == 'review_sync':
                if not can_manage:
                    raise PermissionDenied
                result = run_review_sync(day, actor=request.user, dry_run=False, limit=20)
                c = result['counts']
                messages.info(
                    request,
                    f"متابعة البيانات من العمليات الحالية: {c['created']} جديدة، "
                    f"{c['resolved']} اكتملت، {c['reopened']} أعيد فتحها. "
                    'لم تُعدّل مواد المخزون أو الأسعار أو الوصفات.',
                )

            elif action == 'review_preview':
                task_id = request.POST.get('task_id', '')
                if not task_id.isdecimal():
                    raise ValidationError('اختر متابعة صالحة من مهام اليوم.')
                task = get_object_or_404(
                    BusinessDayTask, pk=task_id, business_day=day,
                    source_type='OperationalReviewCase',
                )
                if not task.source_id.isdecimal():
                    raise ValidationError('رابط المتابعة غير صالح. اطلب مراجعة الإدارة.')
                case = get_object_or_404(OperationalReviewCase, pk=task.source_id)
                recheck_preview = preview_case_recheck(day, case, actor=request.user)

            elif action == 'review_confirm':
                result = confirm_case_recheck(
                    day, actor=request.user, token=request.POST.get('confirmation_token', ''),
                )
                messages.success(request, 'تم تحديث المتابعة بناءً على السجل الأصلي.' if result['changed'] else 'حالة المتابعة لم تتغير.')

            elif action == 'manual_add':
                if not can_manage:
                    raise PermissionDenied
                assigned_to = None
                assigned_to_id = (request.POST.get('assigned_to') or '').strip()
                if assigned_to_id:
                    assigned_to = active_staff_queryset().filter(pk=assigned_to_id).first()
                    if assigned_to is None:
                        raise ValidationError('الموظف المعيّن غير صالح.')
                create_manual_task(
                    day,
                    actor=request.user,
                    title=request.POST.get('title_ar', ''),
                    details=request.POST.get('details', ''),
                    due_at=_parse_local_datetime(request.POST.get('due_at')),
                    priority=request.POST.get('priority', OperationalTaskTemplate.Priority.NORMAL),
                    is_required=request.POST.get('is_required') == '1',
                    assigned_to=assigned_to,
                )
                messages.success(request, 'تمت إضافة المهمة إلى يوم العمل.')

            elif action == 'template_add':
                if not can_manage:
                    raise PermissionDenied
                create_task_template(
                    actor=request.user,
                    title=request.POST.get('title_ar', ''),
                    details=request.POST.get('details', ''),
                    weekdays=request.POST.getlist('weekdays'),
                    due_time=_parse_time(request.POST.get('due_time')),
                    priority=request.POST.get('priority', OperationalTaskTemplate.Priority.NORMAL),
                    responsibility_role=request.POST.get('responsibility_role', ''),
                    is_required=request.POST.get('is_required') == '1',
                )
                result = sync_business_day_tasks(day, actor=request.user)
                messages.success(request, f'تم إنشاء الروتين الدوري. مهام جديدة لليوم: {result["created"]}.')

            elif action == 'template_toggle':
                if not can_manage:
                    raise PermissionDenied
                template = get_object_or_404(OperationalTaskTemplate, pk=request.POST.get('template_id'))
                template.is_active = not template.is_active
                template.save(update_fields=['is_active', 'updated_at'])
                sync_business_day_tasks(day, actor=request.user)
                messages.success(request, 'تم تحديث حالة الروتين الدوري.')

            elif action == 'assign':
                if not can_manage:
                    raise PermissionDenied
                task = get_object_or_404(BusinessDayTask, pk=request.POST.get('task_id'), business_day=day)
                assigned_to = None
                assigned_to_id = (request.POST.get('assigned_to') or '').strip()
                if assigned_to_id:
                    assigned_to = active_staff_queryset().filter(pk=assigned_to_id).first()
                    if assigned_to is None:
                        raise ValidationError('الموظف المعيّن غير صالح.')
                assign_task(task, actor=request.user, assigned_to=assigned_to)
                messages.success(request, 'تم تحديث مسؤول المهمة.')

            elif action == 'status':
                task = get_object_or_404(BusinessDayTask, pk=request.POST.get('task_id'), business_day=day)
                if not _can_update_task(request.user, task):
                    raise PermissionDenied
                set_task_status(
                    task,
                    actor=request.user,
                    status=(request.POST.get('status') or '').strip(),
                    note=request.POST.get('completion_note', ''),
                )
                messages.success(request, 'تم تحديث حالة المهمة.')

            else:
                raise ValidationError('إجراء المهمة غير معروف.')
        except ValidationError as exc:
            messages.error(request, _validation_text(exc))
        if recheck_preview is None:
            return redirect('staff_operational_tasks')

    tasks = (
        day.tasks.select_related('assigned_to', 'task_template', 'completed_by').all()
        if day else BusinessDayTask.objects.none()
    )
    # Follow-ups are manager-visible, or visible only to the current
    # checked-in, eligible holder of explicit operations duty. A roster or
    # pending handover alone never authorizes access to a review case.
    all_tasks = list(tasks)
    review_ids = [
        int(t.source_id) for t in all_tasks
        if t.source_type == 'OperationalReviewCase' and t.source_id.isdecimal()
    ]
    review_cases = {
        case.pk: case for case in OperationalReviewCase.objects.filter(pk__in=review_ids)
    }
    filtered = []
    for task in all_tasks:
        if task.source_type != 'OperationalReviewCase':
            filtered.append(task)
            continue
        case = review_cases.get(int(task.source_id)) if task.source_id.isdecimal() else None
        if case is None:
            continue
        holder = case_duty_holder(day, case)
        if not can_manage and (holder is None or holder.pk != request.user.pk):
            continue
        task.review_case = case
        task.review_holder = holder
        presentation = review_presentation(case, request.user)
        task.review_url = presentation['url']
        task.review_reason = presentation['reason']
        task.review_completion = presentation['completion']
        task.can_recheck = day.status != BusinessDay.Status.CLOSED and can_recheck_case(day, case, request.user)
        filtered.append(task)
    tasks = filtered

    now = timezone.now()
    counts = {
        'pending': sum(task.status == BusinessDayTask.Status.PENDING for task in tasks),
        'overdue': sum(
            task.status == BusinessDayTask.Status.PENDING
            and task.due_at is not None and task.due_at < now for task in tasks
        ),
        'done': sum(task.status == BusinessDayTask.Status.DONE for task in tasks),
    }
    User = get_user_model()
    provider_role = getattr(User.Role, 'INTERNET_PROVIDER', 'internet_provider')
    role_choices = [(value, label) for value, label in User.Role.choices if value != provider_role]

    return render(request, 'operations/tasks.html', {
        'business_day': day,
        'reminders_enabled': reminders_enabled(),
        'tasks': tasks,
        'task_counts': counts,
        'can_manage_tasks': can_manage,
        'recheck_preview': recheck_preview,
        'staff_options': active_staff_queryset() if can_manage else [],
        'task_templates': OperationalTaskTemplate.objects.order_by('-is_active', 'title_ar') if can_manage else [],
        'role_choices': role_choices,
        'weekday_choices': [
            (0, 'الاثنين'), (1, 'الثلاثاء'), (2, 'الأربعاء'), (3, 'الخميس'),
            (4, 'الجمعة'), (5, 'السبت'), (6, 'الأحد'),
        ],
        'now': now,
    })
