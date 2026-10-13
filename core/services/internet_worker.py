"""Production worker cycle for Internet lifecycle and durable network outboxes."""
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.db import close_old_connections
from django.utils import timezone

from core.services.internet_readiness import get_operations_state
from core.services.network_operations import process_ready_network_operations
from core.services.staff_internet_observation import observe_staff_internet_connections
from internet.session_network_operations import process_ready_session_network_operations


def _safe_exception(exc):
    text = str(exc).replace('\r', ' ').replace('\n', ' ')
    lowered = text.lower()
    if any(marker in lowered for marker in (
        'password', 'authorization', 'credential', 'mikrotik_password', 'secret',
    )):
        text = 'Infrastructure operation failed; sensitive details were removed.'
    return f'{type(exc).__name__}: {text[:300]}'


def _record_heartbeat(summary, errors, *, lifecycle_ran):
    """Persist only secret-free operational metadata for staff visibility."""
    state = get_operations_state(create=True)
    now = timezone.now()
    state.last_worker_seen_at = now
    if lifecycle_ran:
        state.last_lifecycle_at = now
    state.last_worker_summary = summary
    state.last_worker_error = '; '.join(error for _, error in errors)[:500]
    state.save(update_fields=(
        'last_worker_seen_at', 'last_lifecycle_at', 'last_worker_summary',
        'last_worker_error', 'updated_at',
    ))


def run_internet_worker_cycle(*, lifecycle_limit=200, network_limit=100,
                              run_lifecycle=True):
    """Run one isolated worker cycle.

    Each concern is deliberately isolated so a temporary lifecycle/database/backend
    failure does not prevent the other durable queues from making progress. Network
    operation failures inside the outboxes remain durable and are represented by the
    processed/succeeded counters rather than raised here.
    """
    summary = {
        'lifecycle': 'skipped' if not run_lifecycle else 'pending',
        'entitlement_network': {'processed': 0, 'succeeded': 0, 'failed': 0},
        'session_network': {'processed': 0, 'succeeded': 0, 'failed': 0},
    }
    errors = []
    close_old_connections()

    if run_lifecycle:
        try:
            # Suppress the human-oriented command output; the worker emits one
            # consolidated summary line instead.
            call_command(
                'reconcile_internet_lifecycle',
                limit=lifecycle_limit,
                stdout=StringIO(),
                verbosity=0,
            )
            summary['lifecycle'] = 'ok'
        except Exception as exc:  # worker boundary: keep the process alive
            summary['lifecycle'] = 'error'
            errors.append(('lifecycle', _safe_exception(exc)))

    try:
        processed, succeeded = process_ready_network_operations(limit=network_limit)
        summary['entitlement_network'] = {
            'processed': processed,
            'succeeded': succeeded,
            'failed': processed - succeeded,
        }
    except Exception as exc:  # worker boundary: session queue must still run
        errors.append(('entitlement_network', _safe_exception(exc)))

    try:
        processed, succeeded = process_ready_session_network_operations(limit=network_limit)
        summary['session_network'] = {
            'processed': processed,
            'succeeded': succeeded,
            'failed': processed - succeeded,
        }
    except Exception as exc:  # worker boundary: retry next cycle
        errors.append(('session_network', _safe_exception(exc)))

    if run_lifecycle and settings.MIKROTIK_ENABLED:
        try:
            summary['staff_connections'] = observe_staff_internet_connections(limit=20)
        except Exception:
            # Do not persist arbitrary exception text from RouterOS or its client.
            errors.append(('staff_connections', 'Read-only staff connection observation unavailable'))

    try:
        _record_heartbeat(summary, errors, lifecycle_ran=run_lifecycle)
    except Exception as exc:  # heartbeat observability must never stop queue progress
        errors.append(('heartbeat', _safe_exception(exc)))

    close_old_connections()
    return summary, errors
