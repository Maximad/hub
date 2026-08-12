"""Durable Internet network-operation outbox and its bounded worker."""
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import InternetEntitlement, InternetNetworkOperation
from core.services.network_backends import get_network_backend

STALE_AFTER = timedelta(minutes=15)


def _safe_error(exc):
    # Keep operational context, never exception repr/arguments which may contain credentials.
    text = str(exc).replace('\r', ' ').replace('\n', ' ')
    for marker in ('password=', 'Authorization:', 'MIKROTIK_PASSWORD='):
        if marker.lower() in text.lower():
            return 'Network operation failed; sensitive details were removed.'
    return text[:500]


def enqueue_network_operation(entitlement, operation, *, reason='', idempotency_key=None,
                              process_after_commit=True):
    """Create one durable logical operation and optionally attempt it after commit."""
    key = idempotency_key or f'entitlement:{entitlement.public_code}:network:{operation}'
    job, _ = InternetNetworkOperation.objects.get_or_create(
        idempotency_key=key,
        defaults={'entitlement': entitlement, 'operation': operation, 'reason': reason[:200]},
    )
    if job.entitlement_id != entitlement.pk or job.operation != operation:
        raise ValueError('Network-operation idempotency key belongs to another action.')
    if process_after_commit:
        transaction.on_commit(lambda job_id=job.pk: process_network_operation(job_id))
    return job


def _claim(operation_id=None):
    now = timezone.now()
    stale = now - STALE_AFTER
    ready = (Q(status__in=(InternetNetworkOperation.Status.PENDING,
                           InternetNetworkOperation.Status.FAILED)) &
             (Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now)))
    ready |= Q(status=InternetNetworkOperation.Status.PROCESSING, last_attempt_at__lt=stale)
    with transaction.atomic():
        queryset = InternetNetworkOperation.objects.select_for_update(skip_locked=True).filter(ready)
        if operation_id is not None:
            queryset = queryset.filter(pk=operation_id)
        job = queryset.order_by('created_at').first()
        if not job:
            return None
        job.status = job.Status.PROCESSING
        job.attempt_count += 1
        job.last_attempt_at = now
        job.next_attempt_at = None
        job.save(update_fields=('status', 'attempt_count', 'last_attempt_at',
                                'next_attempt_at', 'updated_at'))
        return job.pk


def _execute_claimed(claimed_id):
    job = InternetNetworkOperation.objects.select_related('entitlement').get(pk=claimed_id)
    entitlement = job.entitlement
    try:
        backend = get_network_backend(entitlement.network_backend)
        getattr(backend, f'{job.operation}_access')(entitlement)
    except Exception as exc:
        now = timezone.now()
        delay = (1, 5, 15)[min(max(job.attempt_count - 1, 0), 2)]
        InternetNetworkOperation.objects.filter(pk=job.pk).update(
            status=job.Status.FAILED, last_error=_safe_error(exc),
            next_attempt_at=now + timedelta(minutes=delay), updated_at=now)
        InternetEntitlement.objects.filter(pk=entitlement.pk).update(
            network_status=InternetEntitlement.NetworkStatus.PROVISION_ERROR,
            last_network_error=_safe_error(exc), last_network_sync_at=now, updated_at=now)
        return False
    now = timezone.now()
    InternetNetworkOperation.objects.filter(pk=job.pk).update(
        status=job.Status.SUCCEEDED, completed_at=now, last_error='',
        next_attempt_at=None, updated_at=now)
    return True


def process_network_operation(operation):
    """Claim and execute one operation; failures remain durable and never raise."""
    operation_id = operation.pk if isinstance(operation, InternetNetworkOperation) else operation
    claimed_id = _claim(operation_id)
    return False if claimed_id is None else _execute_claimed(claimed_id)


def process_ready_network_operations(*, limit=100):
    processed = succeeded = 0
    while processed < limit:
        job_id = _claim()
        if job_id is None:
            break
        succeeded += bool(_execute_claimed(job_id))
        processed += 1
    return processed, succeeded
