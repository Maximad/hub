from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

ADMIN_ONLY_MESSAGE = 'هذه الصفحة مخصصة للمدير فقط.'
MANAGER_REQUIRED_MESSAGE = 'هذه العملية تحتاج صلاحية المدير أو صاحب المحل.'
ACCESS_DENIED_MESSAGE = 'لا تملك صلاحية الوصول إلى هذه الصفحة.'


# One capability vocabulary is shared by server-side authorization, staff UI,
# and notification routing. Role membership supplies defaults; non-admin users
# may then receive an explicit per-user allow/deny override.
CAPABILITY_LABELS = {
    'staff_home': 'مساحة العمليات',
    'orders': 'الطلبات',
    'pos': 'إدخال الطلبات / نقطة البيع',
    'cashier': 'الكاشير والتحصيل',
    'reports': 'التقارير',
    'finance': 'المالية',
    'inventory': 'المخزون',
    'reservations': 'الحجوزات',
    'events': 'الفعاليات',
    'settings': 'الإعدادات',
    'imports': 'الاستيراد',
    'users': 'المستخدمون والصلاحيات',
    'modifiers': 'خيارات المنتجات',
    'internet_billing': 'فواتير الإنترنت',
    'members/internet': 'الأعضاء والإنترنت',
    'kitchen_board': 'لوحة التحضير',
    'partial_payment_approval': 'الموافقة على الدفع الجزئي',
    'order_edit': 'تعديل الطلب',
    'delivery_management': 'إدارة التوصيل',
}

_ALL_CAPABILITIES = frozenset(CAPABILITY_LABELS)

# Preserve existing operational duties while tightening preparation access:
# kitchen users handle kitchen prep, cashiers retain preparation access because
# bar/cashier/service prep stations resolve to the cashier operator role, and
# waiters no longer get the board merely because they can enter orders.
ROLE_DEFAULT_CAPABILITIES = {
    'admin': _ALL_CAPABILITIES,
    'cashier': frozenset({
        'staff_home', 'orders', 'pos', 'cashier', 'finance', 'inventory',
        'internet_billing', 'members/internet', 'kitchen_board', 'order_edit',
        'delivery_management',
    }),
    'waiter': frozenset({
        'staff_home', 'orders', 'pos', 'reservations', 'events', 'order_edit',
        'delivery_management',
    }),
    'kitchen': frozenset({
        'staff_home', 'inventory', 'kitchen_board',
    }),
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


def role_default_has_capability(role, capability):
    return capability in ROLE_DEFAULT_CAPABILITIES.get(role or '', frozenset())


def _override_map(user):
    if not getattr(user, 'pk', None):
        return {}
    cache_name = '_staff_capability_override_cache'
    cached = getattr(user, cache_name, None)
    if cached is not None:
        return cached
    overrides = {
        row.capability: bool(row.allowed)
        for row in user.staff_capability_overrides.all()
        if row.capability in CAPABILITY_LABELS
    }
    setattr(user, cache_name, overrides)
    return overrides


def clear_staff_capability_cache(user):
    if hasattr(user, '_staff_capability_override_cache'):
        delattr(user, '_staff_capability_override_cache')


def user_has_capability(user, capability):
    if capability not in CAPABILITY_LABELS or not _is_authenticated_active(user):
        return False

    # Admin is deliberately a full-access role. Per-user overrides are intended
    # for operational roles; they cannot silently cripple the last admin path.
    if getattr(user, 'is_superuser', False) or _role(user) == 'admin':
        return True

    overrides = _override_map(user)
    if capability in overrides:
        return overrides[capability]
    return role_default_has_capability(_role(user), capability)


def get_staff_capabilities(user):
    return {name: user_has_capability(user, name) for name in CAPABILITY_LABELS}


def get_staff_capability_details(user):
    overrides = {} if is_owner_or_admin(user) else _override_map(user)
    details = []
    for capability, label in CAPABILITY_LABELS.items():
        allowed = user_has_capability(user, capability)
        if is_owner_or_admin(user):
            source = 'admin'
            source_label = 'مدير — سماح كامل'
        elif capability in overrides:
            source = 'override_allow' if overrides[capability] else 'override_deny'
            source_label = 'سماح فردي' if overrides[capability] else 'منع فردي'
        else:
            source = 'role'
            source_label = 'حسب الدور'
        details.append({
            'key': capability,
            'label': label,
            'allowed': allowed,
            'source': source,
            'source_label': source_label,
        })
    return details


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


# Compatibility map for callers that introspect the old function registry.
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
    'members/internet': lambda user: user_has_capability(user, 'members/internet'),
    'kitchen_board': can_access_kitchen_board,
    'partial_payment_approval': can_approve_partial_payment,
    'order_edit': can_edit_order,
    'delivery_management': can_manage_delivery,
}


def require_staff_capability(capability, message=ACCESS_DENIED_MESSAGE):
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if user_has_capability(request.user, capability):
                return view_func(request, *args, **kwargs)
            messages.error(request, message)
            if can_access_staff_home(request.user):
                return redirect('staff_home')
            return redirect('admin:login')
        return wrapper
    return decorator
