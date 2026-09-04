from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

ADMIN_ONLY_MESSAGE = 'هذه الصفحة مخصصة للمدير فقط.'
MANAGER_REQUIRED_MESSAGE = 'هذه العملية تحتاج صلاحية المدير أو صاحب المحل.'
ACCESS_DENIED_MESSAGE = 'لا تملك صلاحية الوصول إلى هذه الصفحة.'

CAPABILITY_LABELS = {
    'staff_home': 'مساحة العمل',
    'orders': 'الطلبات',
    'pos': 'نقطة البيع',
    'cashier': 'الكاشير والتحصيل',
    'reports': 'التقارير',
    'finance': 'المالية',
    'inventory': 'المخزون',
    'reservations': 'الحجوزات',
    'events': 'الفعاليات',
    'settings': 'الإعدادات',
    'imports': 'الاستيراد',
    'users': 'إدارة المستخدمين',
    'modifiers': 'إضافات وخيارات المنتجات',
    'internet_billing': 'فوترة الإنترنت',
    'members/internet': 'الأعضاء والإنترنت',
    'kitchen_board': 'لوحة التحضير',
    'partial_payment_approval': 'الموافقات الإدارية',
    'order_edit': 'تعديل الطلبات',
    'delivery_management': 'إدارة التوصيل',
}


def _role(user):
    return getattr(user, 'role', '')


def _is_authenticated_active(user):
    return bool(user and user.is_authenticated and user.is_active)


def is_owner_or_admin(user):
    return _is_authenticated_active(user) and (user.is_superuser or _role(user) == 'admin')


def is_cashier(user):
    return _is_authenticated_active(user) and _role(user) == 'cashier'


def is_waiter(user):
    return _is_authenticated_active(user) and _role(user) == 'waiter'


def is_kitchen(user):
    return _is_authenticated_active(user) and _role(user) == 'kitchen'


def can_access_staff_home(user):
    return is_owner_or_admin(user) or is_cashier(user) or is_waiter(user) or is_kitchen(user)


def can_access_orders(user):
    return is_owner_or_admin(user) or is_cashier(user) or is_waiter(user)


def can_access_pos(user):
    return is_owner_or_admin(user) or is_cashier(user) or is_waiter(user)


def can_access_cashier(user):
    return is_owner_or_admin(user) or is_cashier(user)


def can_access_reports(user):
    return is_owner_or_admin(user)


def can_access_finance(user):
    return is_owner_or_admin(user) or is_cashier(user)


def can_access_inventory(user):
    return is_owner_or_admin(user) or is_cashier(user) or is_kitchen(user)


def can_access_reservations(user):
    return is_owner_or_admin(user) or is_waiter(user)


def can_access_events(user):
    return is_owner_or_admin(user) or is_waiter(user)


def can_access_settings(user):
    return is_owner_or_admin(user)


def can_access_imports(user):
    return is_owner_or_admin(user)


def can_access_users(user):
    return is_owner_or_admin(user)


def can_access_modifiers(user):
    return is_owner_or_admin(user)


def can_access_internet_billing(user):
    return is_owner_or_admin(user) or is_cashier(user)


def can_access_kitchen_board(user):
    # Preparation is a dedicated operational capability. Waiters/cashiers can
    # still receive ready/order events through their own capabilities without
    # gaining access to the full preparation board.
    return is_owner_or_admin(user) or is_kitchen(user)


def can_approve_partial_payment(user):
    return is_owner_or_admin(user)


def can_edit_order(user):
    return is_owner_or_admin(user) or is_cashier(user) or is_waiter(user)


def can_manage_delivery(user):
    return is_owner_or_admin(user) or is_cashier(user) or is_waiter(user)


CAPABILITY_CHECKS = {
    'staff_home': can_access_staff_home,
    'orders': can_access_orders,
    'pos': can_access_pos,
    'cashier': can_access_cashier,
    'reports': can_access_reports,
    'finance': can_access_finance,
    'inventory': can_access_inventory,
    'reservations': can_access_reservations,
    'events': can_access_events,
    'settings': can_access_settings,
    'imports': can_access_imports,
    'users': can_access_users,
    'modifiers': can_access_modifiers,
    'internet_billing': can_access_internet_billing,
    'members/internet': can_access_internet_billing,
    'kitchen_board': can_access_kitchen_board,
    'partial_payment_approval': can_approve_partial_payment,
    'order_edit': can_edit_order,
    'delivery_management': can_manage_delivery,
}


def _capability_override(user, capability):
    if not getattr(user, 'pk', None):
        return None
    try:
        return user.capability_overrides.filter(capability=capability).values_list('allowed', flat=True).first()
    except (AttributeError, TypeError):
        return None


def user_has_capability(user, capability):
    checker = CAPABILITY_CHECKS.get(capability)
    if checker is None or not _is_authenticated_active(user):
        return False

    # Keep Django superusers as an emergency all-access path.
    if getattr(user, 'is_superuser', False):
        return True

    override = _capability_override(user, capability)
    if override is not None:
        return bool(override)
    return bool(checker(user))


def get_staff_capabilities(user):
    return {name: user_has_capability(user, name) for name in CAPABILITY_CHECKS}


def get_capability_overrides(user):
    if not getattr(user, 'pk', None):
        return {}
    return dict(user.capability_overrides.values_list('capability', 'allowed'))


def require_staff_capability(capability, message=ACCESS_DENIED_MESSAGE):
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if user_has_capability(request.user, capability):
                return view_func(request, *args, **kwargs)
            messages.error(request, message)
            if user_has_capability(request.user, 'staff_home'):
                return redirect('staff_home')
            return redirect('admin:login')
        return wrapper
    return decorator
