"""Read-only staff presentation of existing session/network evidence."""
from django.core.exceptions import ObjectDoesNotExist

from core.internet_billing import _managed_metered_network_activated
from core.models import InternetSession
from core.services.internet_operations import safe_operation_error


def session_recovery_context(session, *, can_retry=False):
    billing_ready = _managed_metered_network_activated(session)
    try:
        activated_at = session.network_state.network_activated_at
    except ObjectDoesNotExist:
        activated_at = None
    source = session.entitlement if session.entitlement_id else session
    code = source.network_status
    labels = {
        'not_provisioned': 'بانتظار تجهيز الاتصال',
        'provisioned': 'جاهز للاتصال',
        'provision_error': 'تعذر تجهيز الاتصال',
        'disconnected': 'أُوقف الوصول إلى الشبكة',
        'expired': 'انتهت صلاحية الوصول',
    }
    label = labels.get(code, 'حالة الاتصال غير مؤكدة')
    if session.network_provider == InternetSession.NetworkProvider.MANUAL and not session.entitlement_id:
        label = 'اتصال يُدار يدوياً'
    if not billing_ready:
        billing_text = 'لم يبدأ احتساب الوقت لأن تفعيل الشبكة لم يكتمل.'
    elif session.billing_mode == InternetSession.BillingMode.FREE:
        billing_text = 'هذه جلسة مجانية.'
    elif session.billing_mode == InternetSession.BillingMode.PREPAID:
        billing_text = 'تطبق قواعد الرصيد أو الباقة المرتبطة بهذه الجلسة.'
    elif activated_at:
        billing_text = 'بدأ احتساب الوقت عند نجاح التفعيل.'
    else:
        billing_text = 'الفوترة حسب سجل الجلسة.'

    operations = list(session.session_network_operations.order_by('-created_at', '-pk')[:10])
    operation_labels = {'provision': 'تجهيز الاتصال', 'refresh': 'تحديث الوصول', 'disconnect': 'إيقاف الوصول'}
    status_labels = {'pending': 'في الانتظار', 'processing': 'قيد التنفيذ', 'failed': 'فشلت', 'succeeded': 'اكتملت'}
    for job in operations:
        job.action_label = operation_labels.get(job.operation, 'عملية شبكة')
        job.state_label = status_labels.get(job.status, 'غير مؤكدة')
        job.safe_error = safe_operation_error(job.last_error) if job.status == 'failed' else ''
        job.can_retry = bool(can_retry and job.status == 'failed' and (
            job.operation == 'disconnect' or session.status == InternetSession.Status.ACTIVE))
    return {'billing_ready': billing_ready, 'billing_text': billing_text,
            'activated_at': activated_at, 'network_label': label, 'operations': operations,
            'uses_entitlement': bool(session.entitlement_id), 'can_retry': can_retry}
