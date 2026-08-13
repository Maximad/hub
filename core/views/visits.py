import logging
import uuid

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.models import ActivityLog, HubVisit, InternetEntitlement, InternetPackage, InternetSession, TableArea
from core.services.internet_access import end_usage_session
from core.services.visit_internet import (create_visit_internet_sale_and_start, customer_packages,
    self_service_enabled, start_existing_visit_entitlement, usable_member_entitlements)
from core.services.visits import issue_visit_credential, resolve_visit_credential, set_visit_cookie
from core.settings_helpers import get_system_settings
from members.benefits import resolve_internet_price
from members.services import resolve_member_from_request

logger = logging.getLogger(__name__)


def _error_text(error):
    if isinstance(error, ValidationError):
        return next(iter(error.messages), 'تعذر بدء الإنترنت. يمكنك طلب المساعدة من الفريق.')
    return 'تعذر بدء الإنترنت. يمكنك طلب المساعدة من الفريق.'


def _internet_context(visit=None, member=None):
    packages = customer_packages(member)
    for package in packages:
        package.customer_price_syp = int(resolve_internet_price(member, package)[0])
    entitlements = usable_member_entitlements(visit) if visit else InternetEntitlement.objects.none()
    sessions = (visit.internet_sessions.select_related('entitlement', 'package')
                .order_by('-start_time') if visit else InternetSession.objects.none())
    return {'internet_packages': packages, 'internet_entitlements': entitlements,
            'internet_sessions': sessions, 'internet_request_key': uuid.uuid4()}


def current_visit(request):
    system_settings = get_system_settings()
    if not system_settings.customer_visits_enabled:
        return redirect('menu_public')
    credential = resolve_visit_credential(request)
    if not credential:
        return redirect('menu_public')
    visit = credential.visit
    orders = visit.orders.exclude(status='cancelled').prefetch_related('items', 'discounts', 'payments').order_by('created_at')
    menu_url = reverse('menu_table', kwargs={'qr_token': visit.table.qr_token}) if visit.table_id else reverse('menu_public')
    context = {'visit': visit, 'orders': orders, 'menu_url': menu_url,
               'internet_self_service_enabled': self_service_enabled(system_settings)}
    if context['internet_self_service_enabled']:
        context.update(_internet_context(visit, visit.member))
    return render(request, 'menu/current_visit.html', context)


@require_POST
def visit_internet_purchase_start(request):
    system_settings = get_system_settings()
    if not self_service_enabled(system_settings):
        messages.error(request, 'خدمة الإنترنت الذاتية غير متاحة حالياً.')
        return redirect('menu_public')
    raw_cookie = None
    try:
        package = get_object_or_404(InternetPackage, public_code=request.POST.get('package'))
        table = (TableArea.objects.filter(qr_token=request.POST.get('table')).first()
                 if request.POST.get('table') else None)
        credential = resolve_visit_credential(request)
        member_context = resolve_member_from_request(request)
        with transaction.atomic():
            if credential:
                visit = credential.visit
                if table and visit.table_id != table.pk:
                    raise ValidationError('الجلسة مرتبطة بطاولة أخرى. يرجى طلب المساعدة من الفريق.')
            else:
                if not table:
                    raise ValidationError('الجلسة مغلقة.')
                visit = HubVisit.objects.create(table=table,
                    member=member_context.member if member_context else None)
                credential, raw_cookie = issue_visit_credential(visit)
                ActivityLog.objects.create(action='visit.created', details={
                    'visit_id': visit.pk, 'source': 'public_internet'})
                ActivityLog.objects.create(action='visit.browser_bound', details={'visit_id': visit.pk})
            member = visit.member
            if member_context and visit.member_id is None:
                visit.member = member = member_context.member
                visit.save(update_fields=['member', 'updated_at'])
                ActivityLog.objects.create(action='visit.member_auto_attached', details={
                    'visit_id': visit.pk, 'member_id': member.pk})
            elif member_context and visit.member_id != member_context.member.pk:
                raise ValidationError('تعذر مطابقة العضوية. يرجى طلب المساعدة من الفريق.')
            create_visit_internet_sale_and_start(visit=visit, credential=credential,
                package=package, request_key=request.POST.get('request_key', ''), member=member)
        messages.success(request, 'بدأت جلسة الإنترنت. إذا احتجت كلمة مرور الشبكة، اطلبها من الفريق.')
        response = redirect('current_visit')
        return set_visit_cookie(response, raw_cookie) if raw_cookie else response
    except (ValidationError, InternetPackage.DoesNotExist) as exc:
        messages.error(request, _error_text(exc))
        return redirect(request.META.get('HTTP_REFERER') or reverse('menu_public'))
    except Exception:
        logger.exception('Customer visit Internet purchase/start failed')
        messages.error(request, 'تعذر بدء الإنترنت. يمكنك طلب المساعدة من الفريق.')
        return redirect(request.META.get('HTTP_REFERER') or reverse('menu_public'))


@require_POST
def visit_internet_entitlement_start(request, public_code):
    if not self_service_enabled(get_system_settings()):
        messages.error(request, 'خدمة الإنترنت الذاتية غير متاحة حالياً.')
        return redirect('menu_public')
    credential = resolve_visit_credential(request)
    if not credential:
        messages.error(request, 'الجلسة مغلقة.')
        return redirect('menu_public')
    try:
        entitlement = get_object_or_404(InternetEntitlement, public_code=public_code)
        start_existing_visit_entitlement(visit=credential.visit, credential=credential,
                                         entitlement=entitlement)
        messages.success(request, 'بدأت جلسة الإنترنت.')
    except ValidationError as exc:
        messages.error(request, _error_text(exc))
    return redirect('current_visit')


@require_POST
def visit_internet_session_stop(request, public_code):
    if not self_service_enabled(get_system_settings()):
        messages.error(request, 'خدمة الإنترنت الذاتية غير متاحة حالياً.')
        return redirect('menu_public')
    credential = resolve_visit_credential(request)
    if not credential:
        messages.error(request, 'الجلسة مغلقة.')
        return redirect('menu_public')
    session = get_object_or_404(InternetSession, public_code=public_code,
                                visit=credential.visit,
                                status=InternetSession.Status.ACTIVE)
    ended = end_usage_session(session)
    ActivityLog.objects.create(action='visit.internet_session_ended', details={
        'visit_id': credential.visit_id, 'session_id': ended.pk,
        'entitlement_id': ended.entitlement_id,
    })
    messages.success(request, 'تم إيقاف استخدام الإنترنت. وقت الباقة المحددة غير المستخدم لا يُستعاد.')
    return redirect('current_visit')
