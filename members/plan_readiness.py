"""Read-only readiness assessment for memberships; no sales, entitlements or edits.

This is a *configuration* preflight, not a network availability guarantee.
Operational support is still defined by the existing checkout/benefit engines.
"""
from django.core.exceptions import ValidationError

from members.internet_benefits import _prepare
from members.models import MembershipBenefitRule
from members.plan_presentation import staff_plan_presentations


def assess_membership_plan(plan):
    """Return actionable configuration findings, without modifying any record.

    Block only conditions known to fail commercial Internet provisioning after
    payment. Advisory benefits remain visible to operators, never silently
    removed from a purchased plan or misrepresented as automatic.
    """
    errors = []
    warnings = []
    prepared = []
    rules = [rule for rule in plan.benefit_rules.all() if rule.is_active]

    for rule in rules:
        if rule.benefit_type != MembershipBenefitRule.BenefitType.INTERNET_MINUTES:
            continue
        try:
            # The same validation is used by provision_subscription_internet().
            # It resolves partner rules, device bounds and bandwidth profiles.
            prepared.append(_prepare(None, rule.as_snapshot()))
        except (ValidationError, ValueError, TypeError) as exc:
            reason = ' '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
            errors.append(f'مخصص إنترنت #{rule.pk}: {reason[:240]}')

    allocated = sum(item.allocation for item in prepared)
    if allocated > int(plan.price_syp):
        errors.append(
            f'مخصصات الإنترنت المالية ({allocated}) تتجاوز سعر العضوية ({plan.price_syp}).'
        )
    # There is a real operational member-Internet-price resolver, but the plan
    # sale UI does not guarantee a specific package or redemption workflow.
    presentation = staff_plan_presentations([plan])[0]
    if presentation['configured_only']:
        warnings.append(
            'قواعد مُعدّة وليست مزايا تلقائية مؤكدة: '
            + '، '.join(sorted(set(presentation['configured_only'])))
        )
    if rules and not presentation['benefits'] and not presentation['configured_only']:
        warnings.append('لا توجد مزايا تشغيلية يمكن تأكيدها من القواعد الحالية.')

    return {
        'plan': plan,
        'errors': errors,
        'warnings': warnings,
        'supported_benefits': presentation['benefits'],
        'configured_only': presentation['configured_only'],
        'can_sell': not errors,
    }


def validate_membership_sale_configuration(plan):
    assessment = assess_membership_plan(plan)
    if assessment['errors']:
        raise ValidationError({
            'plan': 'لا يمكن بيع خطة ذات مخصص إنترنت غير صالح: '
                    + ' '.join(assessment['errors'])
        })
    return assessment


def staff_plan_sale_presentations(plans):
    """Only enrich existing compact staff plan previews with blocking findings."""
    rows = staff_plan_presentations(plans)
    for row in rows:
        findings = assess_membership_plan(row['plan'])
        row['sale_errors'] = findings['errors']
        row['needs_review'] = bool(findings['warnings'])
    return rows
