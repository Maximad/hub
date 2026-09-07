from dataclasses import dataclass

from django.conf import settings
from django.core import signing

from core.models import Member
from member_accounts.models import MemberAccount
from members.services import get_active_member_context


MEMBER_CARD_SIGNING_SALT = 'hub.member-card.v1'
DEFAULT_MEMBER_CARD_MAX_AGE_SECONDS = 90


@dataclass(frozen=True)
class MemberCardContext:
    member: Member
    account: MemberAccount
    membership: object | None


def member_card_max_age_seconds():
    return int(getattr(settings, 'MEMBER_CARD_QR_MAX_AGE', DEFAULT_MEMBER_CARD_MAX_AGE_SECONDS))


def issue_member_card_token(member):
    """Return a short-lived signed member-card token with no reusable login secret."""
    return signing.dumps(
        {'v': 1, 'member': str(member.public_code)},
        salt=MEMBER_CARD_SIGNING_SALT,
        compress=False,
    )


def resolve_member_card_token(token, *, max_age=None):
    """Resolve a member card token, rejecting expired, malformed, and locked accounts."""
    age = member_card_max_age_seconds() if max_age is None else max_age
    try:
        payload = signing.loads(
            token,
            salt=MEMBER_CARD_SIGNING_SALT,
            max_age=age,
        )
    except (signing.BadSignature, signing.SignatureExpired, TypeError, ValueError):
        return None

    if not isinstance(payload, dict) or payload.get('v') != 1:
        return None
    public_code = payload.get('member')
    if not public_code:
        return None

    member = Member.objects.filter(public_code=public_code).first()
    if member is None or not getattr(member, 'is_active', True):
        return None
    account = MemberAccount.objects.filter(member=member).first()
    if account is None or account.status != MemberAccount.Status.ACTIVE:
        return None

    return MemberCardContext(
        member=member,
        account=account,
        membership=get_active_member_context(member),
    )
