"""Read-only, bounded staff HotSpot observation: no network configuration mutations.

A successful REST response establishes online-login counts at that moment only.
The snapshot stores no MAC addresses, remote session IDs or credentials.
"""
from datetime import timedelta

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from core.models import InternetEntitlement
from core.services.network_backends import MikroTikNetworkBackend
from internet.models import InternalStaffInternetGrant, StaffInternetConnectionSnapshot


STALE_AFTER = timedelta(minutes=3)


def observation_is_fresh(snapshot, *, at=None):
    return bool(snapshot and snapshot.checked_at
                and snapshot.checked_at >= (at or timezone.now()) - STALE_AFTER
                and snapshot.state == StaffInternetConnectionSnapshot.State.OBSERVED)


def _read_owned_sessions(backend, entitlement):
    """Ownership-check the RouterOS user before reading its active sessions."""
    username = backend.username(entitlement)
    owner = backend._owned_user(entitlement)
    if owner is None or owner.get('disabled') == 'true':
        return StaffInternetConnectionSnapshot.State.MISSING, None, None
    rows = backend.client.active_session_details(username)
    if not isinstance(rows, list):
        raise ValueError('invalid RouterOS active session response')
    sessions = [row for row in rows if isinstance(row, dict) and row.get('user') == username]
    if len(sessions) != len(rows):
        raise ValueError('RouterOS active session response contained unexpected users')
    macs = [str(row.get('mac-address') or '').strip().upper() for row in sessions]
    # A session count is trustworthy without MAC; a distinct-device count is not.
    devices = len(set(macs)) if all(macs) else None
    return StaffInternetConnectionSnapshot.State.OBSERVED, len(sessions), devices


def observe_staff_internet_connections(*, limit=20, client=None, at=None):
    """Inspect at most limit active MikroTik staff grants and store count summaries.

    Oldest observation first so a large team rotates through bounded batches.
    Errors are isolated per staff grant and never include raw router responses.
    """
    if not 1 <= int(limit) <= 100:
        raise ValueError('limit must be between 1 and 100')
    if not settings.MIKROTIK_ENABLED:
        return {'checked': 0, 'observed': 0, 'failed': 0, 'disabled': True}
    now = at or timezone.now()
    grants = list(
        InternalStaffInternetGrant.objects.filter(
            user__is_active=True,
            entitlement__network_backend='mikrotik',
            entitlement__status=InternetEntitlement.Status.ACTIVE,
        )
        .select_related('entitlement', 'user')
        .order_by(F('connection_snapshot__checked_at').asc(nulls_first=True), 'pk')[:limit]
    )
    if not grants:
        return {'checked': 0, 'observed': 0, 'failed': 0}
    try:
        backend = MikroTikNetworkBackend(client=client)
    except Exception:
        backend = None
    observed = failed = 0
    for grant in grants:
        state = StaffInternetConnectionSnapshot.State.ERROR
        count = devices = None
        code = ''
        if backend is None:
            code = 'backend_unavailable'
        else:
            try:
                state, count, devices = _read_owned_sessions(backend, grant.entitlement)
                if state == StaffInternetConnectionSnapshot.State.MISSING:
                    code = 'owned_user_unavailable'
            except Exception:
                # Deliberately avoid including arbitrary router error text in any
                # persistent record or report.
                code = 'observation_failed'
        if state == StaffInternetConnectionSnapshot.State.OBSERVED:
            observed += 1
        elif state == StaffInternetConnectionSnapshot.State.ERROR:
            failed += 1
        StaffInternetConnectionSnapshot.objects.update_or_create(
            grant_id=grant.pk,
            defaults={
                'state': state, 'active_login_count': count,
                'distinct_device_count': devices, 'checked_at': now,
                'last_success_at': now if state == StaffInternetConnectionSnapshot.State.OBSERVED
                    else StaffInternetConnectionSnapshot.objects.filter(grant_id=grant.pk)
                        .values_list('last_success_at', flat=True).first(),
                'error_code': code,
            },
        )
    return {'checked': len(grants), 'observed': observed, 'failed': failed}
