from datetime import datetime, time

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import BusinessDay, BusinessDayTask, OperationalTaskTemplate
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
        return redirect('staff_operational_tasks')

    tasks = (
        day.tasks.select_related('assigned_to', 'task_template', 'completed_by').all()
        if day else BusinessDayTask.objects.none()
    )
    now = timezone.now()
    counts = {
        'pending': tasks.filter(status=BusinessDayTask.Status.PENDING).count() if day else 0,
        'overdue': tasks.filter(
            status=BusinessDayTask.Status.PENDING,
            due_at__isnull=False,
            due_at__lt=now,
        ).count() if day else 0,
        'done': tasks.filter(status=BusinessDayTask.Status.DONE).count() if day else 0,
    }
    User = get_user_model()
    provider_role = getattr(User.Role, 'INTERNET_PROVIDER', 'internet_provider')
    role_choices = [(value, label) for value, label in User.Role.choices if value != provider_role]

    return render(request, 'operations/tasks.html', {
        'business_day': day,
        'tasks': tasks,
        'task_counts': counts,
        'can_manage_tasks': can_manage,
        'staff_options': active_staff_queryset() if can_manage else [],
        'task_templates': OperationalTaskTemplate.objects.order_by('-is_active', 'title_ar') if can_manage else [],
        'role_choices': role_choices,
        'weekday_choices': [
            (0, 'الاثنين'), (1, 'الثلاثاء'), (2, 'الأربعاء'), (3, 'الخميس'),
            (4, 'الجمعة'), (5, 'السبت'), (6, 'الأحد'),
        ],
        'now': now,
    })
