"""Private, read-only customer-layout preview. Never reveals a nonpublic source."""
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import render
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from core.models import HomepageFeature
from core.services.public_homepage import DAMASCUS, public_homepage_context


@never_cache
@require_safe
def homepage_preview(request):
    if not (request.user.is_authenticated and request.user.is_active and request.user.is_superuser):
        return HttpResponseForbidden()
    mode = request.GET.get('mode', 'draft')
    if mode not in {'draft', 'scheduled'}:
        return HttpResponseBadRequest('وضع المعاينة غير صالح.')
    at = request.GET.get('at', '')
    try:
        now = parse_datetime(at) if at and len(at) <= 64 else None
    except (ValueError, OverflowError):
        now = None
    if at and now is None:
        return HttpResponseBadRequest('تاريخ المعاينة غير صالح.')
    now = now or timezone.now()
    if timezone.is_naive(now):
        now = timezone.make_aware(now, DAMASCUS)
    feature = HomepageFeature.objects.select_related('image').first() if mode == 'draft' else None
    context = public_homepage_context(now=now, preview_feature=feature)
    reason = {
        'selected': 'تم اختيار المحتوى البارز.',
        'not_configured': 'لا يوجد إعداد بارز؛ تظهر النتيجة الافتراضية.',
        'disabled': 'الإعداد معطل أو غير منشور؛ يظهر البديل.',
        'outside_schedule': 'الوقت خارج نافذة النشر؛ يظهر البديل.',
        'ineligible_source': 'المصدر غير مؤهل للعرض العام؛ يظهر البديل.',
    }[context['feature_resolution']]
    context['homepage_preview'] = {
        'mode': mode, 'at': timezone.localtime(now, DAMASCUS).strftime('%Y-%m-%dT%H:%M'),
        'reason': reason,
    }
    response = render(request, 'public/homepage.html', context)
    response['X-Robots-Tag'] = 'noindex, nofollow'
    patch_vary_headers(response, ['Cookie'])
    return response
