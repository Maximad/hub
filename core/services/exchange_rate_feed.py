"""Trusted parsing, staging, and manually authorized adoption of market-rate data.

No caller may automatically mark a candidate approved or post accounting entries.
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import ExchangeRate, ImportedMarketRate, LocalExchangeRatePolicy


EXPECTED_SOURCE_URL = "https://lirascope.syria-cloud.sy/api/v1/rates/latest?currencies=USD&lang=en"
PUBLIC_RATE_URL = "https://raw.githubusercontent.com/Maximad/hub/data/exchange-rates/rates/latest.json"
DAMASCUS = ZoneInfo("Asia/Damascus")
MAX_SOURCE_AGE = timedelta(hours=36)
MAX_APPROVAL_AGE = timedelta(hours=48)


def _rate(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationError('سعر الصرف غير صالح.') from exc
    if not amount.is_finite() or not (Decimal('1') <= amount <= Decimal('1000')):
        raise ValidationError('سعر الصرف خارج المجال المقبول للعملة السورية الجديدة.')
    return amount


def parse_unapproved_record(value, *, now=None):
    now = now or timezone.now()
    if not isinstance(value, dict) or value.get('schema_version') != 1:
        raise ValidationError('صيغة ملف أسعار الصرف غير مدعومة.')
    required = {
        'provider': 'lirascope', 'reference_market': 'marketRates',
        'city': 'unspecified', 'currency': 'USD', 'denomination': 'SYP_NEW',
        'status': 'unapproved', 'source_url': EXPECTED_SOURCE_URL,
        'denomination_verification': 'numeric_safety_bounds_and_manual_approval',
    }
    if any(value.get(k) != v for k, v in required.items()):
        raise ValidationError('مصدر الأسعار أو وحدة العملة غير متوافقين مع الإعداد المعتمد.')
    try:
        source_at = datetime.fromisoformat(value['source_timestamp_utc'].replace('Z', '+00:00'))
        collected_at = datetime.fromisoformat(value['collected_at_utc'].replace('Z', '+00:00'))
    except (KeyError, AttributeError, ValueError) as exc:
        raise ValidationError('لا يوجد توقيت صالح لسعر الصرف.') from exc
    if source_at.tzinfo is None or collected_at.tzinfo is None:
        raise ValidationError('توقيت السعر يحتاج منطقة زمنية صريحة.')
    source_at = source_at.astimezone(dt_timezone.utc)
    collected_at = collected_at.astimezone(dt_timezone.utc)
    if (source_at > now + timedelta(minutes=15) or now - source_at > MAX_SOURCE_AGE
            or collected_at < source_at or collected_at > now + timedelta(minutes=15)):
        raise ValidationError('توقيت سعر الصرف قديم أو غير متّسق.')
    day = source_at.astimezone(DAMASCUS).date()
    if value.get('business_date_damascus') != day.isoformat():
        raise ValidationError('تاريخ العمل لا يطابق توقيت المصدر في دمشق.')
    digest = value.get('raw_sha256', '')
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValidationError('بصمة الاستجابة الأصلية غير صالحة.')
    buy, sell = _rate(value.get('buy_syp_per_usd')), _rate(value.get('sell_syp_per_usd'))
    if buy > sell or sell > buy * Decimal('1.15'):
        raise ValidationError('فارق الشراء والمبيع غير منطقي.')
    fingerprint = hashlib.sha256(
        f'{source_at.isoformat()}|{buy}|{sell}|{digest}'.encode('utf-8')
    ).hexdigest()
    return {
        'source_fingerprint': fingerprint,
        'raw_sha256': digest, 'source_timestamp': source_at,
        'reference_buy_syp': buy, 'reference_sell_syp': sell,
        'business_date': day,
    }


def stage_unapproved_rate(value, *, now=None):
    fields = parse_unapproved_record(value, now=now)
    row, created = ImportedMarketRate.objects.get_or_create(
        source_fingerprint=fields.pop('source_fingerprint'), defaults=fields
    )
    return row, created


def approve_candidate(candidate_id, *, actor):
    """Publish one new immutable ExchangeRate, preserving history and candidate provenance."""
    if not actor.is_authenticated or not actor.is_active or not actor.is_superuser:
        raise ValidationError('اعتماد سعر الصرف مخصص للمستخدم الخارق فقط.')
    with transaction.atomic():
        candidate = ImportedMarketRate.objects.select_for_update().get(pk=candidate_id)
        if candidate.status != ImportedMarketRate.Status.PENDING:
            raise ValidationError('سبق اتخاذ قرار بشأن هذا السعر.')
        now = timezone.now()
        if candidate.source_timestamp > now or now - candidate.source_timestamp > MAX_APPROVAL_AGE:
            raise ValidationError('السعر قديم. اجلب تحديثاً جديداً قبل الاعتماد.')
        last = ExchangeRate.objects.filter(
            foreign_currency='USD', superseded_by__isnull=True
        ).order_by('-effective_date', '-created_at').first()
        if last and candidate.business_date < last.effective_date:
            raise ValidationError('لا يمكن اعتماد سعر أقدم من آخر سعر منشور.')
        policy = LocalExchangeRatePolicy.objects.filter(pk=1).first()
        offset = policy.sweida_offset_syp if policy else Decimal('0')
        buy = candidate.reference_buy_syp + offset
        sell = candidate.reference_sell_syp + offset
        mid = ((buy + sell) / Decimal('2')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if buy < 1 or sell < buy or mid < 1:
            raise ValidationError('فرق السويداء جعل سعر الصرف غير صالح.')
        # Large deviations are flagged for manual investigation, not silently adopted.
        if last and last.rate_to_base and abs(mid - last.rate_to_base) / last.rate_to_base > Decimal('0.25'):
            raise ValidationError('السعر مختلف بأكثر من 25% عن السعر السابق. تحقّق منه يدوياً قبل الاعتماد.')
        new_rate = ExchangeRate(
            foreign_currency='USD', rate_to_base=mid,
            effective_date=candidate.business_date,
            source='LiraScope market reference + Sweida adjustment (manually approved)',
            note=(f'Reference buy {candidate.reference_buy_syp}, sell {candidate.reference_sell_syp}; '
                  f'Sweida offset {offset}; candidate #{candidate.pk}; mid for legacy SYP accounting'),
            created_by=actor,
        )
        new_rate.full_clean()
        new_rate.save()
        candidate.status = ImportedMarketRate.Status.APPROVED
        candidate.applied_offset_syp = offset
        candidate.approved_exchange_rate = new_rate
        candidate.reviewed_by = actor
        candidate.reviewed_at = now
        candidate.save(update_fields=[
            'status', 'applied_offset_syp', 'approved_exchange_rate',
            'reviewed_by', 'reviewed_at', 'updated_at',
        ])
        return new_rate


def reject_candidate(candidate_id, *, actor, reason):
    if not actor.is_authenticated or not actor.is_active or not actor.is_superuser:
        raise ValidationError('رفض أسعار الصرف يتطلب صلاحية المسؤول.')
    if not reason or not reason.strip():
        raise ValidationError('اذكر سبب الرفض.')
    with transaction.atomic():
        item = ImportedMarketRate.objects.select_for_update().get(pk=candidate_id)
        if item.status != ImportedMarketRate.Status.PENDING:
            raise ValidationError('تمت مراجعة هذا السجل سابقاً.')
        item.status = ImportedMarketRate.Status.REJECTED
        item.review_note = reason.strip()[:500]
        item.reviewed_by = actor
        item.reviewed_at = timezone.now()
        item.save(update_fields=['status', 'review_note', 'reviewed_by', 'reviewed_at', 'updated_at'])
        return item
