"""Arabic staff workspace for the independent, opt-in Reminders Engine."""

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.permissions import user_has_capability
from .forms_reminders import ReminderCreateForm
from .models import Reminder
from .reminders import (
    _manager, _recipient, can_access_reminder, close_reminder,
    create_standalone_reminder, reminders_enabled, snooze_reminder,
)
from .services import current_business_date
from .models import BusinessDay


def _displayable_reminders(user, *, show_history=False):
    cutoff = timezone.now() - timedelta(days=30)
    queryset = Reminder.objects.select_related(
        'assigned_to', 'source_task', 'source_task__business_day'
    )
    if show_history:
        queryset = queryset.exclude(status=Reminder.Status.ACTIVE).filter(
            updated_at__gte=cutoff,
        ).order_by('-updated_at', '-pk')
    else:
        queryset = queryset.filter(status=Reminder.Status.ACTIVE).order_by(
            'next_due_at', '-updated_at', 'pk',
        )
    # Resolve each distinct holder/responsibility combination only once per
    # page. Record-level POST mutations still recheck fresh duty ownership.
    items = list(queryset[:250])
    if _manager(user):
        return items
    day = BusinessDay.objects.filter(
        business_date=current_business_date(),
        status__in=[
            BusinessDay.Status.OPEN, BusinessDay.Status.CLOSING,
            BusinessDay.Status.REOPENED,
        ],
    ).first()
    if day is None:
        return []
    recipient_cache = {}
    visible = []
    for item in items:
        key = (item.assigned_to_id, item.responsibility)
        if key not in recipient_cache:
            target = _recipient(item, day)
            recipient_cache[key] = target.pk if target is not None else None
        if recipient_cache[key] == user.pk:
            visible.append(item)
    return visible


@login_required
def staff_reminders(request):
    if (
        not request.user.is_active
        or getattr(request.user, 'role', '') == getattr(
            get_user_model().Role, 'INTERNET_PROVIDER', 'internet_provider'
        )
        or not user_has_capability(request.user, 'staff_home')
    ):
        raise PermissionDenied

    enabled = reminders_enabled()
    manager = _manager(request.user)
    form = ReminderCreateForm() if enabled and manager else None

    if request.method == 'POST':
        if not enabled:
            raise PermissionDenied('التذكيرات غير مفعّلة في النظام.')
        action = (request.POST.get('reminder_action') or '').strip()
        if action == 'create':
            if not manager:
                raise PermissionDenied
            form = ReminderCreateForm(request.POST)
            if form.is_valid():
                data = form.cleaned_data
                try:
                    create_standalone_reminder(
                        actor=request.user,
                        title=data['title_ar'],
                        details=data['details'],
                        due_at=data['due_at'],
                        repeat=data['repeat'],
                        interval_minutes=data['interval_minutes'] or 0,
                        responsibility=data['responsibility'],
                        assigned_to=data['assigned_to'],
                        expires_at=data['expires_at'],
                    )
                except ValidationError as exc:
                    messages.error(
                        request, ' '.join(getattr(exc, 'messages', [str(exc)]))
                    )
                else:
                    messages.success(request, 'تم إنشاء التذكير.')
                    return redirect('staff_reminders')
        elif action in {'complete', 'cancel', 'snooze'}:
            reminder_id = (request.POST.get('reminder_id') or '').strip()
            if not reminder_id.isdecimal():
                raise Http404
            reminder = get_object_or_404(
                Reminder.objects.select_related('assigned_to'),
                pk=reminder_id,
            )
            if not can_access_reminder(request.user, reminder):
                raise PermissionDenied
            try:
                if action == 'snooze':
                    try:
                        minutes = int(request.POST.get('snooze_minutes', ''))
                    except (TypeError, ValueError) as exc:
                        raise ValidationError('مدة التأجيل غير صالحة.') from exc
                    if minutes not in {15, 30, 60, 240}:
                        raise ValidationError('مدة التأجيل غير صالحة.')
                    snooze_reminder(
                        reminder, actor=request.user,
                        until=timezone.now() + timedelta(minutes=minutes),
                    )
                    messages.success(request, 'تم تأجيل التذكير.')
                else:
                    close_reminder(
                        reminder, actor=request.user,
                        cancelled=action == 'cancel',
                    )
                    messages.success(
                        request,
                        'تم إلغاء التذكير.' if action == 'cancel' else 'تم إنجاز التذكير.',
                    )
            except ValidationError as exc:
                messages.error(request, ' '.join(getattr(exc, 'messages', [str(exc)])))
            else:
                return redirect('staff_reminders')
        else:
            raise PermissionDenied

    history = request.GET.get('view') == 'history'
    active_day = BusinessDay.objects.filter(
        business_date=current_business_date(),
        status__in=[
            BusinessDay.Status.OPEN, BusinessDay.Status.CLOSING,
            BusinessDay.Status.REOPENED,
        ],
    ).first()
    items = _displayable_reminders(request.user, show_history=history) if enabled else []
    return render(request, 'operations/reminders.html', {
        'reminders_enabled': enabled,
        'can_manage_reminders': manager,
        'create_form': form,
        'reminders': items,
        'show_history': history,
        'business_day': active_day,
        'now': timezone.now(),
    })
