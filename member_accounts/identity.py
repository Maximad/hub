import hashlib
import hmac
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from member_accounts.models import MemberAccount
from members.models import MemberDeviceToken
from members.services import get_active_member_context


def _digest(raw):
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class MemberIdentity:
    account: MemberAccount
    member: object
    device: MemberDeviceToken
    membership: object | None

    @property
    def active_subscription(self):
        return self.membership.subscription if self.membership else None

    @property
    def active_plan(self):
        return self.membership.plan if self.membership else None


def resolve_member_identity(request, touch=True):
    """Resolve permanent account identity without requiring an active subscription.

    A valid device issued by the pre-account membership flow is authoritative
    enough to promote its pending compatibility account to claimed state. This
    gives existing members a seamless migration instead of forcing re-enrolment.
    """
    raw = request.COOKIES.get(settings.MEMBER_DEVICE_COOKIE_NAME, '')
    try:
        public_id, secret = raw.split('.', 1)
        device = MemberDeviceToken.objects.select_related('member').get(uuid=public_id)
    except (ValueError, MemberDeviceToken.DoesNotExist):
        return None

    now = timezone.now()
    if (
        device.revoked_at
        or (device.expires_at and device.expires_at <= now)
        or not hmac.compare_digest(device.token_hash, _digest(secret))
    ):
        return None

    account, _ = MemberAccount.objects.get_or_create(member=device.member)
    if account.status == MemberAccount.Status.LOCKED:
        return None
    if not account.is_claimed:
        account.mark_claimed(now)

    membership = get_active_member_context(device.member, now, device)
    if touch:
        if not device.last_used_at or now - device.last_used_at > timedelta(hours=1):
            MemberDeviceToken.objects.filter(pk=device.pk, revoked_at__isnull=True).update(last_used_at=now)
        if not account.last_login_at or now - account.last_login_at > timedelta(hours=1):
            MemberAccount.objects.filter(pk=account.pk).update(last_login_at=now)
    return MemberIdentity(account=account, member=device.member, device=device, membership=membership)
