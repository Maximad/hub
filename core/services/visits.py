import hashlib
import secrets

from django.conf import settings
from django.utils import timezone

from core.models import HubVisitBrowserCredential

COOKIE_NAME = 'hub_visit'
COOKIE_MAX_AGE = 60 * 60 * 24 * 30


def token_hash(raw_token):
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def resolve_visit_credential(request, *, touch=True):
    raw_token = request.COOKIES.get(COOKIE_NAME, '')
    if not raw_token or len(raw_token) > 256:
        return None
    credential = (
        HubVisitBrowserCredential.objects.select_related('visit', 'visit__table', 'visit__table__room')
        .filter(token_hash=token_hash(raw_token), revoked_at__isnull=True, visit__status='open')
        .first()
    )
    if credential and touch:
        now = timezone.now()
        HubVisitBrowserCredential.objects.filter(pk=credential.pk).update(last_seen_at=now)
        credential.last_seen_at = now
    return credential


def issue_visit_credential(visit):
    raw_token = secrets.token_urlsafe(32)
    credential = HubVisitBrowserCredential.objects.create(visit=visit, token_hash=token_hash(raw_token))
    return credential, raw_token


def set_visit_cookie(response, raw_token):
    response.set_cookie(
        COOKIE_NAME,
        raw_token,
        max_age=COOKIE_MAX_AGE,
        secure=not settings.DEBUG,
        httponly=True,
        samesite='Lax',
    )
    return response
