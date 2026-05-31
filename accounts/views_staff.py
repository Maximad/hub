from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.forms import StaffUserCreateForm, StaffUserEditForm, StaffUserPasswordForm
from accounts.permissions import ADMIN_ONLY_MESSAGE, require_staff_capability

User = get_user_model()


def _last_active_admin_count():
    return User.objects.filter(is_active=True).filter(Q(is_superuser=True) | Q(role=User.Role.ADMIN)).count()


def _would_remove_last_active_admin(user, new_active=None, new_role=None, new_superuser=None):
    active = user.is_active if new_active is None else new_active
    role = user.role if new_role is None else new_role
    superuser = user.is_superuser if new_superuser is None else new_superuser
    remains_admin = active and (superuser or role == User.Role.ADMIN)
    if remains_admin:
        return False
    was_active_admin = user.is_active and (user.is_superuser or user.role == User.Role.ADMIN)
    return was_active_admin and _last_active_admin_count() <= 1


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
    return render(request, 'staff/users/form.html', {'form': form, 'mode': 'create'})


@require_staff_capability('users', ADMIN_ONLY_MESSAGE)
def staff_user_detail(request, user_id):
    target = get_object_or_404(User, pk=user_id)
    return render(request, 'staff/users/detail.html', {'target_user': target})


@require_staff_capability('users', ADMIN_ONLY_MESSAGE)
def staff_user_edit(request, user_id):
    target = get_object_or_404(User, pk=user_id)
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
            messages.success(request, 'تم تحديث بيانات المستخدم.')
            return redirect('staff_user_detail', user_id=user.pk)
    return render(request, 'staff/users/form.html', {'form': form, 'mode': 'edit', 'target_user': target})


@require_staff_capability('users', ADMIN_ONLY_MESSAGE)
def staff_user_password(request, user_id):
    target = get_object_or_404(User, pk=user_id)
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
