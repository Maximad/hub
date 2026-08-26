"""Production worker cycle for Internet lifecycle and durable network outboxes."""
from io import StringIO

from django.core.management import call_command
from django.db import close_old_connections

from core.services.network_operations import process_ready_network_operations
from internet.session_network_operations import process_ready_session_network_operations


def _safe_exception(exc):
    text = str(exc).replace('\r', ' ').replace('\n', ' ')
    return f'{type(exc).__name__}: {text[:300]}'


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

    close_old_connections()
    return summary, errors
