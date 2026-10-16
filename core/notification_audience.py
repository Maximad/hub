"""Shared compatibility mapping for legacy notification audiences.

These names are audience labels, not staff capabilities or duty assignments.
Do not use them to grant permission to act on an order or operational record.
"""

ROLE_TO_ACCOUNT_ROLES = {
    'admin': ('admin',),
    'cashier': ('cashier',),
    'service': ('waiter',),
    'waiter': ('waiter',),
    'kitchen': ('kitchen',),
}


def notification_roles_for_user(user):
    """Return role audiences compatible with this user's account role.

    Effective capability and active-duty filtering will be applied by the
    separate operational recipient resolver in a future rollout.
    """
    role = getattr(user, 'role', '') or ''
    roles = {role} if role else set()
    for audience, account_roles in ROLE_TO_ACCOUNT_ROLES.items():
        if role in account_roles:
            roles.add(audience)
    return roles
