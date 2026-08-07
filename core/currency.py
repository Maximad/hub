"""Single source of truth for currency conversion and manual-entry risk."""
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

SYP_NEW = 'SYP_NEW'
USD = 'USD'
SUPPORTED_CURRENCIES = ((SYP_NEW, 'ل.س جديدة'), (USD, 'USD / دولار أميركي'))
MONEY_QUANTUM = Decimal('0.01')


def decimal_amount(value):
    """Parse Western/Arabic digits and display separators without using float."""
    text = str(value).strip().translate(str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789'))
    text = text.replace('\u066c', '').replace(',', '').replace(' ', '').replace('\u066b', '.')
    try:
        result = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValidationError('أدخل مبلغاً رقمياً صالحاً.')
    if not result.is_finite() or result < 0:
        raise ValidationError('يجب أن يكون المبلغ صفراً أو موجباً.')
    return result


def convert_to_base(amount, currency, rate=None):
    amount = decimal_amount(amount)
    if currency == SYP_NEW:
        return amount.quantize(MONEY_QUANTUM), Decimal('1')
    if currency != USD or rate is None:
        raise ValidationError('العملة غير مدعومة أو سعر الصرف مفقود.')
    rate = decimal_amount(rate)
    if rate < Decimal('1'):
        raise ValidationError('السعر يجب أن يُكتب ليرة سورية جديدة لكل دولار (وليس السعر المقلوب).')
    return (amount * rate).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP), rate


def applicable_rate(business_date, *, allow_stale=False, user=None):
    from core.models import ExchangeRate
    rate = ExchangeRate.objects.filter(foreign_currency=USD, effective_date__lte=business_date,
                                       superseded_by__isnull=True).order_by('-effective_date', '-created_at').first()
    if not rate:
        raise ValidationError('لا يوجد سعر صرف منشور صالح لهذا التاريخ.')
    age = business_date - rate.effective_date
    maximum = timedelta(days=getattr(settings, 'CURRENCY_RATE_MAX_AGE_DAYS', 3))
    if age > maximum:
        if not allow_stale:
            raise ValidationError(f'سعر الصرف قديم ({age.days} أيام). يلزم تحديثه أو تجاوز مدقق.')
        if not user or not user.has_perm('core.use_stale_exchange_rate'):
            raise PermissionDenied('لا تملك صلاحية استخدام سعر صرف قديم.')
    return rate


@dataclass(frozen=True)
class RiskResult:
    level: str
    base_amount: Decimal
    reason_codes: tuple = field(default_factory=tuple)
    suggested_amount: Decimal | None = None
    suggested_currency: str | None = None
    warning_ar: str = ''
    thresholds: dict = field(default_factory=dict)


def evaluate_currency_risk(*, amount, currency=SYP_NEW, operation='default', exchange_rate=None,
                           converted_base_amount=None, manually_entered=True, expected_amount=None,
                           recent_values=None, **context):
    """Central evaluator. It only advises/blocks; it never mutates the value."""
    amount = decimal_amount(amount)
    base = decimal_amount(converted_base_amount) if converted_base_amount is not None else convert_to_base(amount, currency, exchange_rate)[0]
    raw = getattr(settings, 'CURRENCY_RISK_THRESHOLDS', {}).get(operation) or getattr(settings, 'CURRENCY_RISK_THRESHOLDS', {}).get('default', {})
    thresholds = {key: Decimal(str(raw.get(key, value))) for key, value in
                  {'warning': '5000', 'acknowledgment': '10000', 'manager': '50000'}.items()}
    if not manually_entered:
        return RiskResult('normal', base, thresholds=thresholds)
    reasons, suggestion = [], None
    if currency == SYP_NEW and amount >= thresholds['acknowledgment'] and amount / 100 <= thresholds['acknowledgment']:
        reasons.append('possible_old_syp_x100'); suggestion = (amount / 100).quantize(MONEY_QUANTUM)
    if expected_amount is not None:
        expected = decimal_amount(expected_amount)
        if expected and Decimal('80') <= amount / expected <= Decimal('120'):
            reasons.append('cash_count_x100'); suggestion = (amount / 100).quantize(MONEY_QUANTUM)
    values = [decimal_amount(v) for v in (recent_values or []) if v is not None]
    if len(values) >= 5:
        values.sort(); median = values[len(values) // 2]
        if median and base >= median * 10 and abs(base / 100 - median) <= median / 2:
            reasons.append('recent_median_x100'); suggestion = (amount / 100).quantize(MONEY_QUANTUM)
    if base >= thresholds['manager']:
        level = 'manager_review_required'; reasons.append('manager_threshold')
    elif base >= thresholds['acknowledgment']:
        level = 'acknowledgment_required'; reasons.append('acknowledgment_threshold')
    elif base >= thresholds['warning']:
        level = 'warning'; reasons.append('soft_threshold')
    else:
        level = 'normal'
    warning = ''
    if 'possible_old_syp_x100' in reasons:
        warning = (f'تنبيه: النظام يستخدم الليرة السورية الجديدة. أدخلت {amount:,.0f} ل.س جديدة، '
                   f'وهي تعادل {amount * 100:,.0f} ل.س بالعملة القديمة. إذا كنت تقصد {amount:,.0f} '
                   f'ل.س قديمة، فالقيمة الصحيحة هي {suggestion:,.0f} ل.س جديدة.')
    return RiskResult(level, base, tuple(dict.fromkeys(reasons)), suggestion, None, warning, thresholds)


def enforce_risk(result, *, acknowledged=False, user=None):
    if result.level in {'acknowledgment_required', 'manager_review_required'} and not acknowledged:
        raise ValidationError('يجب تأكيد المبلغ والعملة وسعر الصرف صراحةً قبل الحفظ.')
    if result.level == 'manager_review_required' and (not user or not user.has_perm('core.approve_high_risk_amount')):
        raise PermissionDenied('هذا المبلغ يحتاج موافقة مدير يملك صلاحية اعتماد المبالغ عالية المخاطر.')


def record_snapshot(source, *, operation, field_name, amount, currency, settlement_currency=None,
                    business_date=None, rate_record=None, user=None, acknowledged=False,
                    approved_by=None, automatically_selected=True):
    """Persist the exact conversion/risk decision; never creates a ledger entry."""
    from django.contrib.contenttypes.models import ContentType
    from core.models import CurrencyEntrySnapshot
    business_date = business_date or timezone.localdate()
    if currency == USD and rate_record is None:
        rate_record = applicable_rate(business_date)
    rate = rate_record.rate_to_base if rate_record else Decimal('1')
    base, rate = convert_to_base(amount, currency, rate)
    result = evaluate_currency_risk(amount=amount, currency=currency, operation=operation,
                                    exchange_rate=rate, converted_base_amount=base)
    enforce_risk(result, acknowledged=acknowledged, user=approved_by or user)
    return CurrencyEntrySnapshot.objects.create(
        source_content_type=ContentType.objects.get_for_model(source), source_object_id=str(source.pk),
        operation=operation, field_name=field_name, transaction_currency=currency,
        settlement_currency=settlement_currency or currency, original_amount=decimal_amount(amount),
        exchange_rate_to_base=rate, base_amount_syp=base, exchange_rate_record=rate_record,
        rate_effective_date=getattr(rate_record, 'effective_date', business_date),
        rate_source_snapshot=getattr(rate_record, 'source', ''), rate_selected_automatically=automatically_selected,
        confirmed_by=user, risk_level=result.level, risk_reason_codes=list(result.reason_codes),
        suggested_amount=result.suggested_amount,
        equivalent_old_syp=base * 100 if currency == SYP_NEW else None,
        thresholds_applied={key: str(value) for key, value in result.thresholds.items()},
        acknowledged=acknowledged, approved_by=approved_by,
    )
