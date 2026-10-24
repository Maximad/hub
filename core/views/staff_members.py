"""Membership staff views and guarded sale activation recovery."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from accounts.permissions import is_owner_or_admin, user_has_capability
from members.activation import attempt_membership_sale_activation
from members.models import MembershipSubscription

from core.views_legacy import staff_members, staff_member_new, staff_member_detail, staff_member_subscribe


@login_required
@require_POST
def staff_member_retry_activation(request, member_id, subscription_id):
    """Owner/admin recovery of a documented, already paid membership sale."""
    if not (is_owner_or_admin(request.user)
            and user_has_capability(request.user, 'members/internet')):
        raise PermissionDenied('هذه العملية تتطلب صلاحية المدير.')
    subscription = get_object_or_404(
        MembershipSubscription, pk=subscription_id,
        member__public_code=member_id)
    result = attempt_membership_sale_activation(
        subscription.pk, actor_id=request.user.pk)
    if result.status in ('activated', 'already_active'):
        messages.success(request, 'تم التحقق من الدفع وتفعيل العضوية.')
    elif result.status == 'failed':
        messages.error(request, 'تعذر التفعيل. راجع سجل الخطأ والدفعة قبل إعادة المحاولة.')
    else:
        messages.warning(request, 'هذه العضوية ليست مستحقة للتفعيل حالياً.')
    return redirect('staff_member_detail', member_id=member_id)
