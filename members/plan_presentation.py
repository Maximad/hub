"""Read-only staff explanations of membership plans and supported benefits.

These labels describe configured rules, not a promise that every benefit type
has an operational redemption workflow. Checkout remains authoritative.
"""
from members.models import MembershipBenefitRule, MembershipPlan


TERM_UNITS = {
    MembershipPlan.TermUnit.DAY: 'يوم',
    MembershipPlan.TermUnit.WEEK: 'أسبوع',
    MembershipPlan.TermUnit.MONTH: 'شهر',
    MembershipPlan.TermUnit.YEAR: 'سنة',
}

NOT_AUTOMATIC = {
    MembershipBenefitRule.BenefitType.EVENT_DISCOUNT_PERCENT,
    MembershipBenefitRule.BenefitType.BOOKING_PRIORITY,
    MembershipBenefitRule.BenefitType.WORKSPACE_MINUTES,
    MembershipBenefitRule.BenefitType.FREE_PRODUCT_QUANTITY,
    MembershipBenefitRule.BenefitType.EARLY_ACCESS,
    MembershipBenefitRule.BenefitType.INTERNET_MEMBER_PRICE,
}


def plan_term_summary(plan):
    if plan.term_value and plan.term_unit in TERM_UNITS:
        return f'{plan.term_value} {TERM_UNITS[plan.term_unit]} (تُحسب من تاريخ البداية)'
    if plan.billing_period == MembershipPlan.BillingPeriod.MONTHLY:
        return 'شهر واحد من تاريخ البداية'
    if plan.billing_period == MembershipPlan.BillingPeriod.ANNUAL:
        return 'سنة واحدة من تاريخ البداية'
    if plan.billing_period == MembershipPlan.BillingPeriod.FIXED_TERM:
        return 'تُحدَّد نهاية الاشتراك عند الشراء'
    return 'دون تاريخ انتهاء تلقائي'


def _target(rule):
    if rule.product_id:
        return f'على {rule.product.name_ar}'
    if rule.category_id:
        return f'على فئة {rule.category.name_ar}'
    if rule.item_type or rule.beverage_type or rule.food_type or rule.service_type:
        return 'على أصناف محددة'
    if rule.menu_section_id or rule.tag_id or rule.scope_type:
        return 'بحسب نطاق القاعدة'
    return 'وفق شروط القاعدة'


def _benefit_line(rule):
    kind = rule.benefit_type
    if kind == MembershipBenefitRule.BenefitType.PRODUCT_DISCOUNT_PERCENT:
        amount = rule.value_decimal if rule.value_decimal is not None else rule.discount_percent
        if amount is not None:
            return f'خصم {amount:g}٪ {_target(rule)}', True
    elif kind == MembershipBenefitRule.BenefitType.PRODUCT_DISCOUNT_FIXED:
        amount = rule.value_decimal if rule.value_decimal is not None else rule.discount_amount_syp
        if amount is not None:
            return f'خصم {amount:g} ل.س لكل وحدة {_target(rule)}', True
    elif kind == MembershipBenefitRule.BenefitType.INTERNET_MINUTES:
        minutes = rule.value_integer or rule.included_minutes
        if minutes and minutes > 0:
            return f'{minutes} دقيقة إنترنت عند تفعيل العضوية', True
    return '', False


def staff_plan_presentations(plans):
    """Return safe, display-only summaries for the staff sale form."""
    results = []
    for plan in plans:
        supported, configured = [], []
        for rule in plan.benefit_rules.all():
            if not rule.is_active:
                continue
            label, operational = _benefit_line(rule)
            if operational:
                supported.append(label)
            elif rule.benefit_type in NOT_AUTOMATIC or not operational:
                configured.append(rule.get_benefit_type_display() if rule.benefit_type
                                  else 'قاعدة قديمة تحتاج مراجعة')
        results.append({
            'plan': plan,
            'term': plan_term_summary(plan),
            'benefits': supported,
            'configured_only': configured,
        })
    return results
