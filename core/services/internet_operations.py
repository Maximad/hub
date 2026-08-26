from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import ActivityLog, InternetNetworkOperation
from core.services.internet_readiness import get_operations_state
from core.services.mikrotik import MikroTikError, RouterOSClient
from internet.models import InternetSessionNetworkOperation


def _safe_error(exc):
    text = str(exc).replace('\r', ' ').replace('\n', ' ')
    lowered = text.lower()
    if any(marker in lowered for marker in (
        'password', 'authorization', 'credential', 'mikrotik_password', 'secret',
    )):
        return 'تعذر الاتصال؛ حُجبت التفاصيل الحساسة.'
    return text[:500]


def _operation_model(kind):
    if kind == 'entitlement':
        return InternetNetworkOperation
    if kind == 'session':
        return InternetSessionNetworkOperation
    raise ValidationError('نوع عملية الشبكة غير معروف.')


@transaction.atomic
def requeue_failed_network_operation(*, kind, operation_id, actor=None):
    """Move one failed durable operation back to pending without doing network I/O."""
    model = _operation_model(kind)
    job = model.objects.select_for_update().filter(pk=operation_id).first()
    if job is None:
        raise ValidationError('عملية الشبكة غير موجودة.')
    if job.status != job.Status.FAILED:
        raise ValidationError('يمكن إعادة محاولة العمليات الفاشلة فقط.')

    job.status = job.Status.PENDING
    job.next_attempt_at = None
    job.completed_at = None
    job.save(update_fields=('status', 'next_attempt_at', 'completed_at', 'updated_at'))
    ActivityLog.objects.create(
        actor=actor,
        action='internet.network_operation_requeued',
        details={
            'kind': kind,
            'operation_id': job.pk,
            'operation': job.operation,
            'attempt_count': job.attempt_count,
        },
    )
    return job


def run_readonly_mikrotik_healthcheck(*, actor=None):
    """Probe RouterOS system/resource directly; never mutates router state.

    This preflight is intentionally allowed while MIKROTIK_ENABLED is false so the
    router can be verified before production provisioning is enabled.
    """
    now = timezone.now()
    state = get_operations_state(create=True)
    try:
        client = RouterOSClient(
            base_url=getattr(settings, 'MIKROTIK_BASE_URL', ''),
            username=getattr(settings, 'MIKROTIK_USERNAME', ''),
            password=getattr(settings, 'MIKROTIK_PASSWORD', ''),
            verify_tls=getattr(settings, 'MIKROTIK_VERIFY_TLS', True),
            ca_file=getattr(settings, 'MIKROTIK_CA_FILE', ''),
            connect_timeout=getattr(settings, 'MIKROTIK_CONNECT_TIMEOUT', 5),
            read_timeout=getattr(settings, 'MIKROTIK_READ_TIMEOUT', 10),
        )
        resource = client.system_resource()
        if not isinstance(resource, dict):
            raise ValidationError('استجابة MikroTik غير صالحة.')
        ok = True
        message = 'اتصال MikroTik للقراءة فقط ناجح.'
    except (MikroTikError, ValidationError) as exc:
        ok = False
        message = _safe_error(exc)

    state.last_mikrotik_check_at = now
    state.last_mikrotik_check_ok = ok
    state.last_mikrotik_check_message = message
    state.save(update_fields=(
        'last_mikrotik_check_at', 'last_mikrotik_check_ok',
        'last_mikrotik_check_message', 'updated_at',
    ))
    ActivityLog.objects.create(
        actor=actor,
        action='internet.mikrotik_readonly_healthcheck',
        details={'ok': ok},
    )
    return ok, message
