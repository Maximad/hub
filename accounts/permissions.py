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
    # Preparation-operator compatibility helper. Bartenders use the same prep
    # state machine as kitchen staff while notifications remain station-specific.
    return _is_authenticated_active(user) and _role(user) in {'kitchen', 'bartender'}


def is_bartender(user):
    return _is_authenticated_active(user) and _role(user) == 'bartender'


# Role defaults. These are the baseline policy only; callers should use the
# public can_* helpers or user_has_capability() so per-user overrides apply.
def _default_staff_home(user):
    return (
        is_owner_or_admin(user)
        or is_cashier(user)
        or is_waiter(user)
        or is_kitchen(user)
    )


def _default_orders(user):
    return is_owner_or_admin(user) or is_cashier(user) or is_waiter(user)


def _default_pos(user):
    return is_owner_or_admin(user) or is_cashier(user) or is_waiter(user)


def _default_cashier(user):
    return is_owner_or_admin(user) or is_cashier(user)


def _default_reports(user):
    return is_owner_or_admin(user)


def _default_finance(user):
    return is_owner_or_admin(user) or is_cashier(user)


def _default_inventory(user):
    return is_owner_or_admin(user) or is_cashier(user) or is_kitchen(user)


def _default_reservations(user):
    return is_owner_or_admin(user) or is_waiter(user)


def _default_events(user):
    return is_owner_or_admin(user) or is_waiter(user)


def _default_settings(user):
    return is_owner_or_admin(user)


def _default_imports(user):
    return is_owner_or_admin(user)


def _default_users(user):
    return is_owner_or_admin(user)


def _default_modifiers(user):
    return is_owner_or_admin(user)


def _default_internet_billing(user):
    return is_owner_or_admin(user) or is_cashier(user)


def _default_kitchen_board(user):
    # Kitchen and bar are first-class preparation roles. Cashier access remains
    # for launch compatibility with existing non-kitchen prep workflows.
    return is_owner_or_admin(user) or is_cashier(user) or is_kitchen(user)


def _default_partial_payment_approval(user):
    return is_owner_or_admin(user)


def _default_order_edit(user):
    return is_owner_or_admin(user) or is_cashier(user) or is_waiter(user)


def _default_delivery_management(user):
    return is_owner_or_admin(user) or is_cashier(user) or is_waiter(user)


CAPABILITY_CHECKS = {
    'staff_home': _default_staff_home,
    'orders': _default_orders,
    'pos': _default_pos,
    'cashier': _default_cashier,
    'reports': _default_reports,
    'finance': _default_finance,
    'inventory': _default_inventory,
    'reservations': _default_reservations,
    'events': _default_events,
    'settings': _default_settings,
    'imports': _default_imports,
    'users': _default_users,
    'modifiers': _default_modifiers,
    'internet_billing': _default_internet_billing,
    'members/internet': _default_internet_billing,
    'kitchen_board': _default_kitchen_board,
    'partial_payment_approval': _default_partial_payment_approval,
    'order_edit': _default_order_edit,
    'delivery_management': _default_delivery_management,
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


# Public compatibility helpers. Existing view/action code that calls these
# now receives the same effective permission result as decorators/navigation.
def can_access_staff_home(user):
    return user_has_capability(user, 'staff_home')


def can_access_orders(user):
    return user_has_capability(user, 'orders')


def can_access_pos(user):
    return user_has_capability(user, 'pos')


def can_access_cashier(user):
    return user_has_capability(user, 'cashier')


def can_access_reports(user):
    return user_has_capability(user, 'reports')


def can_access_finance(user):
    return user_has_capability(user, 'finance')


def can_access_inventory(user):
    return user_has_capability(user, 'inventory')


def can_access_reservations(user):
    return user_has_capability(user, 'reservations')


def can_access_events(user):
    return user_has_capability(user, 'events')


def can_access_settings(user):
    return user_has_capability(user, 'settings')


def can_access_imports(user):
    return user_has_capability(user, 'imports')


def can_access_users(user):
    return user_has_capability(user, 'users')


def can_access_modifiers(user):
    return user_has_capability(user, 'modifiers')


def can_access_internet_billing(user):
    return user_has_capability(user, 'internet_billing')


def can_access_kitchen_board(user):
    return user_has_capability(user, 'kitchen_board')


def can_approve_partial_payment(user):
    return user_has_capability(user, 'partial_payment_approval')


def can_edit_order(user):
    return user_has_capability(user, 'order_edit')


def can_manage_delivery(user):
    return user_has_capability(user, 'delivery_management')


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
