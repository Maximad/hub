"""Owner-only market rate staging review and local Sweida adjustment UI."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from core.models import ExchangeRate, ImportedMarketRate, LocalExchangeRatePolicy
from core.services.exchange_rate_feed import approve_candidate, reject_candidate


@login_required
def staff_exchange_rate_settings(request):
    # Disallow mutations and sensitive admin access during View As previews.
    if (not request.user.is_active or not request.user.is_superuser
            or getattr(request, 'view_as_actor', None)):
        raise PermissionDenied('إعدادات أسعار الصرف مخصصة للمسؤول الأعلى.')
    policy = LocalExchangeRatePolicy.objects.filter(pk=1).first()
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'adjustment':
                raw = request.POST.get('sweida_offset_syp', '').strip()
                try:
                    offset = Decimal(raw)
                except (InvalidOperation, ValueError) as exc:
                    raise ValidationError('أدخل فرقاً رقمياً صالحاً.') from exc
                if not offset.is_finite() or abs(offset) > Decimal('50'):
                    raise ValidationError('فرق السويداء يجب أن يقع بين -50 و+50 ليرة جديدة.')
                with transaction.atomic():
                    policy, _ = LocalExchangeRatePolicy.objects.select_for_update().get_or_create(pk=1)
                    policy.sweida_offset_syp = offset
                    policy.updated_by = request.user
                    policy.full_clean()
                    policy.save()
                messages.success(request, 'حُفظ فرق السويداء للعمليات القادمة فقط.')
            elif action == 'approve':
                rate = approve_candidate(request.POST.get('candidate_id'), actor=request.user)
                messages.success(request, f'اعتمدت سعر الصرف المرجعي {rate.rate_to_base} ل.س لكل دولار.')
            elif action == 'reject':
                reject_candidate(request.POST.get('candidate_id'), actor=request.user,
                                 reason=request.POST.get('reason', ''))
                messages.success(request, 'تم رفض السعر مع حفظ السبب.')
            else:
                raise ValidationError('إجراء غير معروف.')
        except (ValidationError, ValueError, InvalidOperation, ImportedMarketRate.DoesNotExist) as error:
            messages.error(request, ' '.join(getattr(error, 'messages', [str(error)])))
        return redirect('staff_exchange_rate_settings')
    candidates = list(ImportedMarketRate.objects.select_related(
        'reviewed_by', 'approved_exchange_rate'
    ).order_by('-source_timestamp')[:30])
    latest = ExchangeRate.objects.filter(
        foreign_currency='USD', superseded_by__isnull=True
    ).order_by('-effective_date', '-created_at').first()
    return render(request, 'staff/exchange_rate_settings.html', {
        'policy': policy, 'offset': policy.sweida_offset_syp if policy else Decimal('0'),
        'candidates': candidates, 'latest': latest,
    })
