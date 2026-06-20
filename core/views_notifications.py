from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, Http404
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST
from accounts.permissions import can_access_staff_home
from core.models import NotificationPreference
from core.notifications import group_notification_recipients_for_user, mark_grouped_notification_read


def _deny(user):
    return not (user.is_authenticated and can_access_staff_home(user))


def _serialize(card):
    return {
        'id': card['group_key'],
        'title': card['title'],
        'message': card['message'],
        'order_number': card['order_number'],
        'station': card['station'],
        'created_at': card['created_at_display'],
        'link': card['link'],
        'is_read': card['is_read'],
    }


@login_required
def staff_notifications(request):
    if _deny(request.user): raise Http404()
    notifications = group_notification_recipients_for_user(request.user, limit=50)
    return render(request, 'staff/notifications.html', {'notifications': notifications})


@login_required
def staff_notifications_poll(request):
    if _deny(request.user): raise Http404()
    latest = group_notification_recipients_for_user(request.user, unread_only=True, limit=10)
    latest_ids = [card['group_key'] for card in latest]
    known_ids = {value for value in (request.GET.get('known') or '').split(',') if value}
    after = request.GET.get('after') or ''
    has_new = bool(latest_ids and (set(latest_ids) - known_ids) and latest_ids[0] != after)
    return JsonResponse({'unread_count': len(group_notification_recipients_for_user(request.user, unread_only=True, limit=500, fetch_limit=500)), 'latest': [_serialize(card) for card in latest], 'latest_ids': latest_ids, 'has_new': has_new, 'server_timestamp': timezone.now().isoformat(), 'html': render(request, 'staff/_notifications_list.html', {'notifications': latest}).content.decode()})


@login_required
@require_POST
def staff_notifications_mark_read(request):
    if _deny(request.user): raise Http404()
    nid = request.POST.get('id') or ''
    recipient_id = int(nid) if nid.isdigit() else None
    updated = mark_grouped_notification_read(request.user, group_key=nid if not recipient_id else None, recipient_id=recipient_id)
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
