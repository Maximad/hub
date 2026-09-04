from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.forms import StaffUserCreateForm, StaffUserEditForm, StaffUserPasswordForm
from accounts.permissions import (
    ADMIN_ONLY_MESSAGE,
    get_staff_capability_details,
    is_owner_or_admin,
    require_staff_capability,
)

User = get_user_model()


def _last_active_admin_count():
    return User.objects.filter(is_active=True).filter(Q(is_superuser=True) | Q(role=User.Role.ADMIN)).count()


def _is_admin_identity(user):
    return bool(getattr(user, 'is_superuser', False) or getattr(user, 'role', '') == User.Role.ADMIN)


def _can_manage_target(actor, target):
    """Delegated user managers may manage operational users, never admins."""
    return is_owner_or_admin(actor) or not _is_admin_identity(target)


def _would_remove_last_active_admin(user, new_active=None, new_role=None, new_superuser=None):
    active = user.is_active if new_active is None else new_active
    role = user.role if new_role is None else new_role
    superuser = user.is_superuser if new_superuser is None else new_superuser
    remains_admin = active and (superuser or role == User.Role.ADMIN)
    if remains_admin:
        return False
    was_active_admin = user.is_active and (user.is_superuser or user.role == User.Role.ADMIN)
    return was_active_admin and _last_active_admin_count() <= 1


def _user_form_context(form, **extra):
    return {
        'form': form,
        'basic_fields': [form[name] for name in form.basic_field_names],
        'capability_fields': [form[name] for name in form.capability_field_names],
        **extra,
    }


def _deny_admin_target(request, target):
    if _can_manage_target(request.user, target):
        return None
    messages.error(request, 'لا يمكنك تعديل حساب مدير بهذه الصلاحية المفوّضة.')
    return redirect('staff_user_detail', user_id=target.pk)


@require_staff_capability('users', ADMIN_ONLY_MESSAGE)
def staff_users_list(request):
    users = User.objects.all().order_by('username')
    query = request.GET.get('q', '').strip()
    role = request.GET.get('role', '').strip()
    active = request.GET.get('active', '').strip()
    admin_access = request.GET.get('admin_access', '').strip()
    if query:
        users = users.filter(
            Q(username__icontains=query) | Q(first_name__icontains=query) | Q(last_name__icontains=query) |
            Q(email__icontains=query) | Q(phone__icontains=query)
        )
    if role:
        users = users.filter(role=role)
    if active == 'yes':
        users = users.filter(is_active=True)
    elif active == 'no':
        users = users.filter(is_active=False)
    if admin_access == 'yes':
        users = users.filter(is_staff=True)
    elif admin_access == 'no':
        users = users.filter(is_staff=False)
    return render(request, 'staff/users/list.html', {
        'users': users,
        'role_choices': User.Role.choices,
        'filters': {'q': query, 'role': role, 'active': active, 'admin_access': admin_access},
    })


@require_staff_capability('users', ADMIN_ONLY_MESSAGE)
def staff_user_new(request):
    form = StaffUserCreateForm(request.POST or None, actor=request.user)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        messages.success(request, f'تم إنشاء المستخدم {user.username}.')
        return redirect('staff_user_detail', user_id=user.pk)
    return render(
        request,
        'staff/users/form.html',
        _user_form_context(form, mode='create'),
    )


@require_staff_capability('users', ADMIN_ONLY_MESSAGE)
def staff_user_detail(request, user_id):
    target = get_object_or_404(User, pk=user_id)
    return render(request, 'staff/users/detail.html', {
        'target_user': target,
        'capability_details': get_staff_capability_details(target),
        'can_manage_target': _can_manage_target(request.user, target),
    })


@require_staff_capability('users', ADMIN_ONLY_MESSAGE)
def staff_user_edit(request, user_id):
    target = get_object_or_404(User, pk=user_id)
    denied = _deny_admin_target(request, target)
    if denied:
        return denied
    form = StaffUserEditForm(request.POST or None, instance=target, actor=request.user)
    if request.method == 'POST' and form.is_valid():
        if _would_remove_last_active_admin(
            target,
            new_active=form.cleaned_data['is_active'],
            new_role=form.cleaned_data['role'],
            new_superuser=form.cleaned_data.get('make_superuser', target.is_superuser),
        ):
            form.add_error(None, 'لا يمكن تعطيل أو إزالة آخر مدير/مالك نشط.')
        else:
            user = form.save()
            messages.success(request, 'تم تحديث بيانات المستخدم وصلاحياته.')
            return redirect('staff_user_detail', user_id=user.pk)
    return render(
        request,
        'staff/users/form.html',
        _user_form_context(form, mode='edit', target_user=target),
    )


@require_staff_capability('users', ADMIN_ONLY_MESSAGE)
def staff_user_password(request, user_id):
    target = get_object_or_404(User, pk=user_id)
    denied = _deny_admin_target(request, target)
    if denied:
        return denied
    form = StaffUserPasswordForm(request.POST or None, user=target)
    if request.method == 'POST' and form.is_valid():
        target.set_password(form.cleaned_data['new_password'])
        target.save(update_fields=['password'])
        messages.success(request, 'تم تعيين كلمة المرور الجديدة.')
        return redirect('staff_user_detail', user_id=target.pk)
    return render(request, 'staff/users/password.html', {'form': form, 'target_user': target})


@require_POST
@require_staff_capability('users', ADMIN_ONLY_MESSAGE)
def staff_user_toggle_active(request, user_id):
    target = get_object_or_404(User, pk=user_id)
    denied = _deny_admin_target(request, target)
    if denied:
        return denied
    if target.pk == request.user.pk:
        messages.error(request, 'لا يمكنك تعطيل حسابك الحالي.')
        return redirect('staff_user_detail', user_id=target.pk)
    new_active = not target.is_active
    if not new_active and _would_remove_last_active_admin(target, new_active=False):
        messages.error(request, 'لا يمكن تعطيل آخر مدير/مالك نشط.')
        return redirect('staff_user_detail', user_id=target.pk)
    target.is_active = new_active
    target.save(update_fields=['is_active'])
    messages.success(request, 'تم تفعيل المستخدم.' if new_active else 'تم تعطيل المستخدم.')
    return redirect('staff_user_detail', user_id=target.pk)
