"""Read-only, bounded staff HotSpot observation: no network configuration mutations.

A successful REST response establishes online-login counts at that moment only.
The snapshot stores no MAC addresses, remote session IDs or credentials.
"""
from datetime import timedelta
import hashlib
import hmac
import re

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from core.models import InternetEntitlement
from core.services.network_backends import MikroTikNetworkBackend
from internet.models import (
    InternalStaffInternetGrant, StaffInternetConnectionSnapshot, StaffInternetDeviceEvidence,
    StaffInternetObservedInterval,
)


STALE_AFTER = timedelta(minutes=3)
DEVICE_EVIDENCE_RETENTION = timedelta(days=30)
MAX_CONTINUITY_GAP = timedelta(seconds=120)
MAX_CLOCK_DIFFERENCE_SECONDS = 5
_MAC_ADDRESS = re.compile(r'[0-9A-F]{2}(?::[0-9A-F]{2}){5}\Z')
_MAX_SAMPLE_SECONDS = 366 * 24 * 3600
_ROUTEROS_UNITS = re.compile(
    r'(?:(?P<w>\d+)w)?(?:(?P<d>\d+)d)?(?:(?P<h>\d+)h)?'
    r'(?:(?P<m>\d+)m)?(?:(?P<s>\d+)s)?'
)
_ROUTEROS_CLOCK = re.compile(
    r'(?:(?P<days>\d+)d)?(?P<hours>\d+):(?P<minutes>\d{2}):(?P<seconds>\d{2})'
)


def parse_routeros_uptime(value):
    """Parse a RouterOS uptime for the current login, not cumulative usage.

    Unknown or malformed durations are not misreported as zero.
    """
    if not isinstance(value, str) or not value:
        return None
    clock = _ROUTEROS_CLOCK.fullmatch(value)
    if clock:
        hours, minutes, seconds = (int(clock.group(n)) for n in ('hours', 'minutes', 'seconds'))
        if minutes > 59 or seconds > 59 or (clock.group('days') and hours > 23):
            return None
        total = (int(clock.group('days') or 0) * 86400
                 + hours * 3600 + minutes * 60 + seconds)
    else:
        parts = _ROUTEROS_UNITS.fullmatch(value)
        if not parts or not any(parts.group(x) is not None for x in ('w', 'd', 'h', 'm', 's')):
            return None
        total = sum(int(parts.group(unit) or 0) * seconds for unit, seconds in (
            ('w', 604800), ('d', 86400), ('h', 3600), ('m', 60), ('s', 1),
        ))
    return total if 0 <= total <= _MAX_SAMPLE_SECONDS else None


def observed_concurrent_limit_state(entitlement, snapshot, *, at=None):
    """Compare fresh distinct-device evidence to configured concurrent limit only.

    Never treat registered-device history or missing MAC metadata as verified.
    """
    if not observation_is_fresh(snapshot, at=at):
        return 'unknown'
    observed = snapshot.distinct_device_count
    limit = entitlement.max_concurrent_devices
    if observed is None or not limit or int(limit) < 1:
        return 'unknown'
    return 'exceeded' if observed > limit else 'within_observed_limit'



def _device_token(grant_id, canonical_mac):
    """Grant-scoped keyed digest; callers never persist raw hardware addresses."""
    source = f'hub:staff-wifi-device:v1:{grant_id}:{canonical_mac}'.encode('ascii')
    return hmac.new(settings.SECRET_KEY.encode('utf-8'), source, hashlib.sha256).hexdigest()


def sampled_continuity_seconds(previous_at, current_at, previous_uptime, current_uptime):
    """Return conservative evidence between adjacent samples, or unknown.

    Connection duration must increase approximately with the wall clock. Router
    reboot, reconnect, dropped polls, missing metadata and duplicate timestamps
    all yield unknown rather than estimated or billable minutes.
    """
    if previous_at is None or not isinstance(previous_uptime, int) or not isinstance(current_uptime, int):
        return None
    elapsed = (current_at - previous_at).total_seconds()
    if not 0 < elapsed <= MAX_CONTINUITY_GAP.total_seconds():
        return None
    delta = current_uptime - previous_uptime
    if delta <= 0 or abs(delta - elapsed) > MAX_CLOCK_DIFFERENCE_SECONDS:
        return None
    credited = int(min(delta, elapsed))
    return credited if credited > 0 else None


def _record_device_evidence(grant, device_uptimes, *, at, previous_snapshot):
    """Update keyed device observations and append only credible short intervals.

    Caller must hold the grant row lock and be inside a transaction. A previous
    complete RouterOS sample is required to accept any new continuity evidence.
    """
    if not device_uptimes:
        return
    preceding_complete = bool(
        previous_snapshot
        and previous_snapshot.state == StaffInternetConnectionSnapshot.State.OBSERVED
        and previous_snapshot.distinct_device_count is not None
        and previous_snapshot.longest_active_uptime_seconds is not None
    )
    current_complete = all(uptime is not None for uptime in device_uptimes.values())
    for mac, uptime in device_uptimes.items():
        token = _device_token(grant.pk, mac)
        record, created = StaffInternetDeviceEvidence.objects.get_or_create(
            grant=grant, device_token=token,
            defaults={
                'first_seen_at': at, 'last_seen_at': at,
                'last_active_uptime_seconds': uptime,
            },
        )
        if created or at <= record.last_seen_at:
            continue
        if (
            preceding_complete and current_complete
            and record.last_seen_at == previous_snapshot.checked_at
        ):
            seconds = sampled_continuity_seconds(
                record.last_seen_at, at, record.last_active_uptime_seconds, uptime,
            )
            if seconds is not None:
                StaffInternetObservedInterval.objects.create(
                    device=record, start_at=record.last_seen_at,
                    end_at=at, observed_seconds=seconds,
                )
        record.last_seen_at = at
        record.last_active_uptime_seconds = uptime
        record.save(update_fields=('last_seen_at', 'last_active_uptime_seconds'))


def observation_is_fresh(snapshot, *, at=None):
    return bool(snapshot and snapshot.checked_at
                and snapshot.checked_at >= (at or timezone.now()) - STALE_AFTER
                and snapshot.state == StaffInternetConnectionSnapshot.State.OBSERVED)


def _read_owned_sessions(backend, entitlement):
    """Ownership-check the RouterOS user before reading its active sessions."""
    username = backend.username(entitlement)
    owner = backend._owned_user(entitlement)
    if owner is None or owner.get('disabled') == 'true':
        return StaffInternetConnectionSnapshot.State.MISSING, None, None, None, {}
    rows = backend.client.active_session_details(username)
    if not isinstance(rows, list):
        raise ValueError('invalid RouterOS active session response')
    sessions = [row for row in rows if isinstance(row, dict) and row.get('user') == username]
    if len(sessions) != len(rows):
        raise ValueError('RouterOS active session response contained unexpected users')
    macs = [str(row.get('mac-address') or '').strip().upper() for row in sessions]
    # A session count is trustworthy without MAC; only complete *valid* address
    # evidence may identify distinct network addresses or extend device history.
    addresses_complete = all(_MAC_ADDRESS.fullmatch(mac) for mac in macs)
    devices = len(set(macs)) if addresses_complete else None
    # Missing or unparseable uptime means the longest active duration is unknown.
    durations = [parse_routeros_uptime(row.get('uptime')) for row in sessions]
    longest = (max(durations) if durations and all(value is not None for value in durations)
               else (0 if not durations else None))
    device_uptimes = {}
    if addresses_complete:
        for mac, duration in zip(macs, durations):
            if mac not in device_uptimes:
                device_uptimes[mac] = duration
            elif duration is None or device_uptimes[mac] is None:
                device_uptimes[mac] = None
            else:
                device_uptimes[mac] = max(device_uptimes[mac], duration)
    return (
        StaffInternetConnectionSnapshot.State.OBSERVED,
        len(sessions), devices, longest, device_uptimes,
    )


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
    # A short retention ceiling applies even if all later router observations fail.
    StaffInternetDeviceEvidence.objects.filter(
        last_seen_at__lt=now - DEVICE_EVIDENCE_RETENTION,
    ).delete()
    StaffInternetObservedInterval.objects.filter(
        end_at__lt=now - DEVICE_EVIDENCE_RETENTION,
    ).delete()
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
        count = devices = longest_uptime = None
        device_uptimes = {}
        code = ''
        if backend is None:
            code = 'backend_unavailable'
        else:
            try:
                state, count, devices, longest_uptime, device_uptimes = _read_owned_sessions(
                    backend, grant.entitlement,
                )
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
        with transaction.atomic():
            # Remote REST reads happened outside the transaction. Serialize
            # writes by grant to avoid duplicate intervals or stale overwrites
            # when two worker instances observe in different orders.
            InternalStaffInternetGrant.objects.select_for_update().get(pk=grant.pk)
            previous_snapshot = StaffInternetConnectionSnapshot.objects.filter(
                grant_id=grant.pk,
            ).first()
            if previous_snapshot and previous_snapshot.checked_at and now <= previous_snapshot.checked_at:
                continue
            if state == StaffInternetConnectionSnapshot.State.OBSERVED:
                _record_device_evidence(
                    grant, device_uptimes, at=now, previous_snapshot=previous_snapshot,
                )
            StaffInternetConnectionSnapshot.objects.update_or_create(
                grant_id=grant.pk,
                defaults={
                    'state': state, 'active_login_count': count,
                    'distinct_device_count': devices,
                    'longest_active_uptime_seconds': longest_uptime, 'checked_at': now,
                    'last_success_at': now if state == StaffInternetConnectionSnapshot.State.OBSERVED
                        else StaffInternetConnectionSnapshot.objects.filter(grant_id=grant.pk)
                            .values_list('last_success_at', flat=True).first(),
                    'error_code': code,
                },
            )
    return {'checked': len(grants), 'observed': observed, 'failed': failed}
