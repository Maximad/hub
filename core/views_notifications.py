from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, Http404
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST
from accounts.permissions import can_access_staff_home
from core.models import NotificationPreference
from core.notifications import link_for_event, visible_recipients_for


def _deny(user):
    return not (user.is_authenticated and can_access_staff_home(user))


def _serialize(recipient):
    event = recipient.notification_event
    order_number = event.order.display_number if event.order_id else ''
    station = event.target_station.name_ar if event.target_station_id else ''
    return {'id': recipient.id, 'title': event.title_ar, 'message': event.message_ar, 'order_number': order_number, 'station': station, 'created_at': event.created_at.strftime('%Y-%m-%d %H:%M'), 'link': link_for_event(event)}


@login_required
def staff_notifications(request):
    if _deny(request.user): raise Http404()
    qs = visible_recipients_for(request.user).select_related('notification_event','notification_event__order','notification_event__target_station').filter(notification_event__is_active=True).order_by('-created_at')[:50]
    return render(request, 'staff/notifications.html', {'notifications': qs})


@login_required
def staff_notifications_poll(request):
    if _deny(request.user): raise Http404()
    qs = visible_recipients_for(request.user).select_related('notification_event','notification_event__order','notification_event__target_station').filter(notification_event__is_active=True, is_read=False)
    latest = list(qs.order_by('-created_at')[:10])
    after = request.GET.get('after') or ''
    has_new = any(str(r.id) != after for r in latest[:1]) if latest else False
    return JsonResponse({'unread_count': qs.count(), 'latest': [_serialize(r) for r in latest], 'has_new': has_new, 'server_timestamp': timezone.now().isoformat(), 'html': render(request, 'staff/_notifications_list.html', {'notifications': latest}).content.decode()})


@login_required
@require_POST
def staff_notifications_mark_read(request):
    if _deny(request.user): raise Http404()
    qs = visible_recipients_for(request.user).filter(is_read=False)
    nid = request.POST.get('id')
    if nid and nid.isdigit(): qs = qs.filter(pk=int(nid))
    updated = qs.update(is_read=True, read_at=timezone.now(), delivered_at=timezone.now())
    return JsonResponse({'ok': True, 'updated': updated})


@login_required
@require_POST
def staff_notifications_preferences(request):
    if _deny(request.user): raise Http404()
    pref, _ = NotificationPreference.objects.get_or_create(user=request.user)
    for field in ['enable_sound','enable_browser_notifications']:
        if field in request.POST:
            setattr(pref, field, request.POST.get(field) in {'1','true','on'})
    pref.save()
    return JsonResponse({'ok': True, 'enable_sound': pref.enable_sound, 'enable_browser_notifications': pref.enable_browser_notifications})
