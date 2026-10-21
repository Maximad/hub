"""Staff consent for one browser during its existing Hub visit; no network writes."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.permissions import user_has_capability
from core.models import ActivityLog, HubVisit, HubVisitBrowserCredential, InternetSession, SystemSetting


APPROVAL = HubVisitBrowserCredential.FastInternetApproval
MESSAGE = 'الإنترنت السريع يحتاج موافقة الموظف لهذا الجهاز. أرسل طلب الموافقة أولاً.'


def approval_required():
    # Do not use the per-process settings cache for an access-control switch.
    value = SystemSetting.objects.order_by('-updated_at', '-pk').values_list(
        'fast_internet_staff_approval_required', flat=True,
    ).first()
    return True if value is None else value


def _credential(visit, credential):
    if not credential or visit.status != HubVisit.Status.OPEN:
        raise ValidationError('الجلسة مغلقة.')
    current = HubVisitBrowserCredential.objects.select_for_update().filter(
        pk=credential.pk, visit=visit, revoked_at__isnull=True,
    ).first()
    if current is None:
        raise ValidationError('تعذر التحقق من جهاز هذه الزيارة.')
    return current


def approval_context(credential):
    state = APPROVAL.NOT_REQUESTED
    if credential:
        state = HubVisitBrowserCredential.objects.filter(pk=credential.pk, revoked_at__isnull=True).values_list(
            'fast_internet_approval', flat=True,
        ).first() or state
    return {'fast_approval_required': approval_required() and state != APPROVAL.APPROVED,
            'fast_approval_state': state, 'fast_approval_reference': credential.pk if credential else None}


def require_fast_internet_approval(visit, credential, *, entitlement=None):
    """Called under the visit lock before creating charges/sessions/network jobs."""
    if not approval_required():
        return
    current = _credential(visit, credential)
    if entitlement and entitlement.origin_type in {'internal_owner_grant', 'internal_team_grant'}:
        # Ownership is checked by the caller before this exemption.
        return
    if current.fast_internet_approval == APPROVAL.APPROVED:
        return
    # Preserve already-live access when the switch is enabled. This query does not
    # authorize a different browser or new visit, or confuse basic Wi-Fi with fast.
    from core.services.visit_internet_devices import browser_session_queryset
    if browser_session_queryset(current).filter(status=InternetSession.Status.ACTIVE).filter(
        guest_wifi_grant__isnull=True,
    ).exists():
        return
    raise ValidationError(MESSAGE)


@transaction.atomic
def request_approval(*, visit, credential, guest_name='', guest_phone='', member=None):
    from core.services.fast_internet_identity import ensure_fast_visit_identity
    from core.services.visit_internet import self_service_enabled
    from core.settings_helpers import get_system_settings
    visit = HubVisit.objects.select_for_update().get(pk=visit.pk)
    current = _credential(visit, credential)
    if not self_service_enabled(get_system_settings()):
        raise ValidationError('خدمة الإنترنت الذاتية غير متاحة حالياً.')
    if not approval_required():
        return current
    ensure_fast_visit_identity(visit, guest_name=guest_name, guest_phone=guest_phone, member=member)
    if current.fast_internet_approval in {APPROVAL.PENDING, APPROVAL.APPROVED, APPROVAL.REJECTED}:
        return current
    current.fast_internet_approval = APPROVAL.PENDING
    current.fast_internet_requested_at = timezone.now()
    current.save(update_fields=['fast_internet_approval', 'fast_internet_requested_at', 'updated_at'])
    ActivityLog.objects.create(action='internet.fast_approval_requested', details={
        'visit_id': visit.pk, 'credential_id': current.pk,
    })
    return current


@transaction.atomic
def review_approval(*, credential_id, actor, decision, expected_state=APPROVAL.PENDING):
    if not user_has_capability(actor, 'members/internet'):
        raise PermissionDenied('مراجعة الطلب تحتاج صلاحية إدارة الإنترنت.')
    if decision not in {APPROVAL.APPROVED, APPROVAL.REJECTED}:
        raise ValidationError('اختر الموافقة أو رفض الطلب.')
    # Same aggregate lock order as customer starts and visit closure.
    visit_id = HubVisitBrowserCredential.objects.get(pk=credential_id).visit_id
    visit = HubVisit.objects.select_for_update().get(pk=visit_id)
    current = _credential(visit, HubVisitBrowserCredential(pk=credential_id))
    if current.fast_internet_approval == decision:
        return current
    if (current.fast_internet_approval != expected_state or expected_state not in {APPROVAL.PENDING, APPROVAL.REJECTED}
            or (expected_state == APPROVAL.REJECTED and decision != APPROVAL.APPROVED)):
        raise ValidationError('هذا الطلب حُسم أو لم يُرسل بعد. حدّث الصفحة.')
    current.fast_internet_approval = decision
    current.fast_internet_reviewed_at = timezone.now()
    current.fast_internet_reviewed_by = actor
    current.save(update_fields=['fast_internet_approval', 'fast_internet_reviewed_at', 'fast_internet_reviewed_by', 'updated_at'])
    ActivityLog.objects.create(actor=actor, action='internet.fast_approval_reviewed', details={
        'visit_id': visit.pk, 'credential_id': current.pk, 'decision': decision,
    })
    return current
