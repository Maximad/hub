"""Durable network-operation outbox for package-less InternetSession access."""
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from internet.models import InternetSessionNetworkOperation, InternetSessionNetworkState
from internet.session_network_backends import PROVISION_ERROR, get_session_network_backend


STALE_AFTER = timedelta(minutes=15)


def _safe_error(exc):
    text = str(exc).replace('\r', ' ').replace('\n', ' ')
    lowered = text.lower()
    if any(marker in lowered for marker in (
        'password', 'authorization', 'credential', 'mikrotik_password',
    )):
        return 'Network operation failed; sensitive details were removed.'
    return text[:500]


def enqueue_session_network_operation(session, operation, *, reason='', idempotency_key=None,
                                      process_after_commit=True):
    key = idempotency_key or f'session:{session.public_code}:network:{operation}'
    job, _ = InternetSessionNetworkOperation.objects.get_or_create(
        idempotency_key=key,
        defaults={'session': session, 'operation': operation, 'reason': reason[:200]},
    )
    if job.session_id != session.pk or job.operation != operation:
        raise ValueError('Session network-operation idempotency key belongs to another action.')
    if process_after_commit:
        transaction.on_commit(lambda job_id=job.pk: process_session_network_operation(job_id))
    return job


def _claim(operation_id=None):
    now = timezone.now()
    stale = now - STALE_AFTER
    ready = (
        Q(status__in=(
            InternetSessionNetworkOperation.Status.PENDING,
            InternetSessionNetworkOperation.Status.FAILED,
        ))
        & (Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
    )
    ready |= Q(
        status=InternetSessionNetworkOperation.Status.PROCESSING,
        last_attempt_at__lt=stale,
    )
    with transaction.atomic():
        queryset = (
            InternetSessionNetworkOperation.objects
            .select_for_update(skip_locked=True)
            .filter(ready)
        )
        if operation_id is not None:
            queryset = queryset.filter(pk=operation_id)
        job = queryset.order_by('created_at').first()
        if not job:
            return None
        job.status = job.Status.PROCESSING
        job.attempt_count += 1
        job.last_attempt_at = now
        job.next_attempt_at = None
        job.save(update_fields=(
            'status', 'attempt_count', 'last_attempt_at',
            'next_attempt_at', 'updated_at',
        ))
        return job.pk


def _record_session_failure(session, safe_error, now):
    session.network_status = PROVISION_ERROR
    session.save(update_fields=['network_status', 'updated_at'])
    state, _ = InternetSessionNetworkState.objects.get_or_create(session=session)
    state.last_network_error = safe_error
    state.last_network_sync_at = now
    state.save(update_fields=['last_network_error', 'last_network_sync_at', 'updated_at'])


def _execute_claimed(claimed_id):
    job = InternetSessionNetworkOperation.objects.select_related('session').get(pk=claimed_id)
    session = job.session
    try:
        backend = get_session_network_backend(session.network_provider)
        getattr(backend, f'{job.operation}_access')(session)
    except Exception as exc:
        now = timezone.now()
        delay = (1, 5, 15)[min(max(job.attempt_count - 1, 0), 2)]
        safe_error = _safe_error(exc)
        InternetSessionNetworkOperation.objects.filter(pk=job.pk).update(
            status=job.Status.FAILED,
            last_error=safe_error,
            next_attempt_at=now + timedelta(minutes=delay),
            updated_at=now,
        )
        _record_session_failure(session, safe_error, now)
        return False

    now = timezone.now()
    InternetSessionNetworkOperation.objects.filter(pk=job.pk).update(
        status=job.Status.SUCCEEDED,
        completed_at=now,
        last_error='',
        next_attempt_at=None,
        updated_at=now,
    )
    return True


def process_session_network_operation(operation):
    operation_id = operation.pk if isinstance(operation, InternetSessionNetworkOperation) else operation
    claimed_id = _claim(operation_id)
    return False if claimed_id is None else _execute_claimed(claimed_id)


def process_ready_session_network_operations(*, limit=100):
    processed = succeeded = 0
    while processed < limit:
        job_id = _claim()
        if job_id is None:
            break
        succeeded += bool(_execute_claimed(job_id))
        processed += 1
    return processed, succeeded
