"""Shared table-entry helpers for QR and manually typed table numbers."""
import hashlib
import hmac
import re
import time

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import HubVisit, TableArea


PIN_ATTEMPT_LIMIT = 5
PIN_LOCK_SECONDS = 5 * 60
_ARABIC_DIGIT_TRANSLATION = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789')


def normalize_table_number(value):
    text = str(value or '').translate(_ARABIC_DIGIT_TRANSLATION).strip()
    if not text or not text.isdigit() or len(text) > 6:
        raise ValidationError('أدخل رقم طاولة صالحاً.')
    return str(int(text))


def _numbers_in_text(value):
    normalized = str(value or '').translate(_ARABIC_DIGIT_TRANSLATION)
    return {str(int(match)) for match in re.findall(r'\d+', normalized)}


def table_matches_number(table, number):
    wanted = normalize_table_number(number)
    return wanted in (_numbers_in_text(table.name_ar) | _numbers_in_text(table.name_en))


def resolve_table_number(number):
    wanted = normalize_table_number(number)
    matches = [
        table
        for table in TableArea.objects.select_related('room').order_by('room__name_ar', 'name_ar', 'pk')
        if table_matches_number(table, wanted)
    ]
    if not matches:
        raise ValidationError('رقم الطاولة غير موجود.')
    if len(matches) > 1:
        raise ValidationError('رقم الطاولة موجود في أكثر من مكان. استخدم QR الطاولة أو اطلب المساعدة من الفريق.')
    return matches[0]


def visit_join_pin(visit):
    """Return the stable four-digit PIN for an open visit without storing it.

    The PIN is an HMAC of the visit identity under Django's production secret key.
    It therefore never appears in the database and changes naturally with each new
    visit. Four digits are intentionally optimized for in-room sharing rather than
    account-grade authentication, so callers must rate-limit guesses.
    """
    material = f'hub-visit-pin:v1:{visit.pk}:{visit.public_code}'.encode('utf-8')
    digest = hmac.new(settings.SECRET_KEY.encode('utf-8'), material, hashlib.sha256).digest()
    return f'{int.from_bytes(digest[:4], "big") % 10000:04d}'


def _pin_collision_exists(table, pin, *, exclude_visit_id=None):
    visits = HubVisit.objects.filter(table=table, status=HubVisit.Status.OPEN).order_by('pk')
    if exclude_visit_id is not None:
        visits = visits.exclude(pk=exclude_visit_id)
    return any(hmac.compare_digest(visit_join_pin(visit), pin) for visit in visits)


@transaction.atomic
def create_table_visit(table, *, member=None, created_by=None):
    """Create an independent bill on a physical table with a unique open-visit PIN."""
    table = TableArea.objects.select_for_update().get(pk=table.pk)
    for _attempt in range(20):
        visit = HubVisit.objects.create(table=table, member=member, created_by=created_by)
        pin = visit_join_pin(visit)
        if not _pin_collision_exists(table, pin, exclude_visit_id=visit.pk):
            return visit
        # No related records exist yet. Retrying gives the visit a fresh identity,
        # making a four-digit collision vanishingly unlikely without storing a PIN.
        visit.delete()
    raise ValidationError('تعذر إنشاء رمز جلسة قصير فريد. يرجى طلب المساعدة من الفريق.')


def find_open_visit_by_pin(table, raw_pin):
    pin = str(raw_pin or '').translate(_ARABIC_DIGIT_TRANSLATION).strip()
    if not re.fullmatch(r'\d{4}', pin):
        raise ValidationError('أدخل رمز الجلسة المكوّن من 4 أرقام.')
    matches = [
        visit
        for visit in HubVisit.objects.filter(table=table, status=HubVisit.Status.OPEN).order_by('pk')
        if hmac.compare_digest(visit_join_pin(visit), pin)
    ]
    if len(matches) != 1:
        raise ValidationError('رمز الجلسة غير صحيح.')
    return matches[0]


def _attempt_key(table):
    return f'hub_visit_pin_attempts:{table.pk}'


def assert_pin_attempt_allowed(request, table):
    state = request.session.get(_attempt_key(table), {})
    locked_until = float(state.get('locked_until') or 0)
    if locked_until > time.time():
        raise ValidationError('تم إيقاف محاولات الرمز مؤقتاً. جرّب مرة أخرى بعد بضع دقائق.')
    if locked_until:
        request.session.pop(_attempt_key(table), None)


def record_pin_failure(request, table):
    key = _attempt_key(table)
    state = request.session.get(key, {})
    count = int(state.get('count') or 0) + 1
    if count >= PIN_ATTEMPT_LIMIT:
        request.session[key] = {'count': count, 'locked_until': time.time() + PIN_LOCK_SECONDS}
    else:
        request.session[key] = {'count': count}
    request.session.modified = True


def clear_pin_failures(request, table):
    request.session.pop(_attempt_key(table), None)
    request.session.modified = True
