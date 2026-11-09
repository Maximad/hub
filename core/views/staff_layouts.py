"""Staff-only, persistent venue SVG layout lab — Release A (no public booking)."""
import copy
import json

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from accounts.permissions import require_staff_capability
from core.models import ActivityLog
from events.layout_validation import validate_draft_layout, MAX_LAYOUT_BYTES
from events.models import Event, EventLayoutSnapshot, VenueLayoutTemplate, VenueLayoutRevision


@require_staff_capability('events')
@require_GET
def staff_layouts(request):
    templates = list(VenueLayoutTemplate.objects.filter(archived=False).order_by('kind', 'name', 'pk'))
    event_layouts = list(
        EventLayoutSnapshot.objects.select_related('event', 'template')
        .order_by('-event__starts_at')[:60]
    )
    events_without = Event.objects.exclude(
        pk__in=EventLayoutSnapshot.objects.values('event_id')
    ).order_by('-starts_at')[:80]
    return render(request, 'staff/layouts/index.html', {
        'templates': templates,
        'event_layouts': event_layouts,
        'events_without': events_without,
    })


@require_staff_capability('events')
@require_GET
def staff_layout_new(request):
    return _render_lab(request, label='تخطيط جديد', target='new')


@require_staff_capability('events')
@require_GET
def staff_layout_edit(request, template_id):
    template = get_object_or_404(VenueLayoutTemplate, pk=template_id, archived=False)
    return _render_lab(
        request, label=template.name, target='template', layout=template.layout,
        pk=template.pk, version=template.version, kind=template.kind,
    )


@require_staff_capability('events')
@require_GET
def staff_event_layout_edit(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    snapshot = EventLayoutSnapshot.objects.filter(event=event).first()
    if snapshot is None:
        messages.warning(request, 'اختر قالباً واحفظ نسخة للفعالية أولاً.')
        return redirect('staff_layouts')
    return _render_lab(
        request, label=event.title_ar, target='event', layout=snapshot.layout,
        pk=event.pk, version=snapshot.version,
        kind=snapshot.template.kind if snapshot.template else VenueLayoutTemplate.Kind.CUSTOM,
    )


def _render_lab(request, *, label, target, layout=None, pk=None, version=0, kind='comedy'):
    if target == 'template':
        history = VenueLayoutRevision.objects.filter(template_id=pk).order_by('-version')[:30]
    elif target == 'event':
        history = VenueLayoutRevision.objects.filter(event_layout__event_id=pk).order_by('-version')[:30]
    else:
        history = []
    config = {
        'label': label, 'target': target, 'id': pk, 'version': version,
        'kind': kind, 'layout': layout,
        'saveUrl': reverse('staff_layout_save'),
        'restoreUrl': reverse('staff_layout_restore'),
        'revisions': [{'id': r.pk, 'version': r.version} for r in history],
        'indexUrl': reverse('staff_layouts'),
    }
    return render(request, 'staff/layouts/lab.html', {'layout_config': config})


@require_staff_capability('events')
@require_POST
def staff_layout_save(request):
    if len(request.body) > MAX_LAYOUT_BYTES + 8_000:
        return JsonResponse({'error': 'المخطط كبير جداً.'}, status=413)
    try:
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValidationError('طلب غير صالح.')
        name = str(payload.get('name') or '').strip()
        kind = payload.get('kind')
        target = payload.get('target')
        if not name or len(name) > 120:
            raise ValidationError('اكتب اسماً للمخطط (حتى 120 حرفاً).')
        if kind not in VenueLayoutTemplate.Kind.values:
            raise ValidationError('نوع الفعالية غير معروف.')
        layout = validate_draft_layout(payload.get('layout'))
        try:
            supplied_version = int(payload.get('version'))
        except (TypeError, ValueError, OverflowError):
            raise ValidationError('نسخة حفظ غير صالحة.')
        if isinstance(payload.get('version'), bool) or supplied_version < 0:
            raise ValidationError('نسخة حفظ غير صالحة.')
        with transaction.atomic():
            if target == 'new':
                template = VenueLayoutTemplate.objects.create(
                    name=name, kind=kind, layout=layout,
                    created_by=request.user, updated_by=request.user,
                )
                VenueLayoutRevision.objects.create(
                    template=template, version=template.version,
                    layout=copy.deepcopy(layout), created_by=request.user,
                )
                ActivityLog.objects.create(
                    actor=request.user, action='venue_layout.template_created',
                    details={'layout_template_id': template.pk, 'seat_count_schematic':
                             layout['seats_proposed']['ground'] + layout['seats_proposed']['mezz']},
                )
                response = {'id': template.pk, 'version': template.version,
                            'url': reverse('staff_layout_edit', args=[template.pk]),
                            'target': 'template'}
            elif target == 'template':
                pk = _positive_pk(payload.get('id'))
                template = get_object_or_404(
                    VenueLayoutTemplate.objects.select_for_update(), pk=pk, archived=False,
                )
                if template.version != supplied_version:
                    return JsonResponse({'error': 'تغيّر المخطط من جلسة أخرى. أعد فتحه قبل الحفظ.'}, status=409)
                template.name, template.kind, template.layout = name, kind, layout
                template.version += 1
                template.updated_by = request.user
                template.save(update_fields=['name', 'kind', 'layout', 'version', 'updated_by', 'updated_at'])
                VenueLayoutRevision.objects.create(
                    template=template, version=template.version,
                    layout=copy.deepcopy(layout), created_by=request.user,
                )
                ActivityLog.objects.create(
                    actor=request.user, action='venue_layout.template_updated',
                    details={'layout_template_id': pk, 'revision': template.version},
                )
                response = {'id': pk, 'version': template.version,
                            'url': reverse('staff_layout_edit', args=[pk]),
                            'target': target}
            elif target == 'event':
                pk = _positive_pk(payload.get('id'))
                snapshot = get_object_or_404(
                    EventLayoutSnapshot.objects.select_for_update().select_related('event'), event_id=pk,
                )
                if snapshot.version != supplied_version:
                    return JsonResponse({'error': 'تم تعديل مخطط الفعالية من جلسة أخرى. أعد تحميله.'}, status=409)
                snapshot.layout = layout
                snapshot.version += 1
                snapshot.updated_by = request.user
                snapshot.save(update_fields=['layout', 'version', 'updated_by', 'updated_at'])
                VenueLayoutRevision.objects.create(
                    event_layout=snapshot, version=snapshot.version,
                    layout=copy.deepcopy(layout), created_by=request.user,
                )
                ActivityLog.objects.create(
                    actor=request.user, action='venue_layout.event_edited',
                    details={'event_id': pk, 'revision': snapshot.version},
                )
                response = {'id': pk, 'version': snapshot.version,
                            'url': reverse('staff_event_layout_edit', args=[pk]),
                            'target': target}
            else:
                raise ValidationError('عملية الحفظ غير معروفة.')
    except (ValidationError, ValueError, TypeError) as error:
        message = ' '.join(error.messages) if isinstance(error, ValidationError) else 'بيانات التخطيط غير صالحة.'
        return JsonResponse({'error': message}, status=400)
    return JsonResponse(response)


def _positive_pk(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 1_000_000_000_000:
        raise ValidationError('رقم التخطيط غير صالح.')
    return value


@require_staff_capability('events')
@require_POST
def staff_layout_assign(request):
    try:
        template_id = _positive_pk(int(request.POST.get('template_id', '')))
        event_id = _positive_pk(int(request.POST.get('event_id', '')))
    except (ValueError, TypeError, ValidationError):
        messages.error(request, 'اختر قالباً وفعالية صالحين.')
        return redirect('staff_layouts')

    with transaction.atomic():
        event = get_object_or_404(Event.objects.select_for_update(), pk=event_id)
        template = get_object_or_404(VenueLayoutTemplate, pk=template_id, archived=False)
        if EventLayoutSnapshot.objects.filter(event=event).exists():
            messages.error(request, 'لهذه الفعالية نسخة مستقلة بالفعل. عدّلها دون استبدالها.')
            return redirect('staff_event_layout_edit', event_id=event.pk)
        try:
            snapshot = EventLayoutSnapshot.objects.create(
                event=event, template=template,
                layout=copy.deepcopy(template.layout),
                assigned_by=request.user, updated_by=request.user,
            )
        except IntegrityError:
            messages.error(request, 'تم إسناد مخطط لهذه الفعالية من جلسة أخرى.')
            return redirect('staff_layouts')
        VenueLayoutRevision.objects.create(
            event_layout=snapshot, version=snapshot.version,
            layout=copy.deepcopy(snapshot.layout), created_by=request.user,
        )
        ActivityLog.objects.create(
            actor=request.user, action='venue_layout.event_assigned',
            details={'event_id': event.pk, 'layout_template_id': template.pk,
                     'event_layout_id': snapshot.pk},
        )
    messages.success(request, 'تم إنشاء نسخة مستقلة للفعالية. هذا لا يفتح حجوزات المقاعد.')
    return redirect('staff_event_layout_edit', event_id=event.pk)


@require_staff_capability('events')
@require_POST
def staff_layout_restore(request):
    """Restore a historical draft by creating a new revision (no destructive rewind)."""
    if len(request.body) > 4096:
        return JsonResponse({'error': 'طلب استعادة كبير جداً.'}, status=413)
    try:
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValidationError('طلب غير صالح.')
        pk = _positive_pk(payload.get('id'))
        revision_id = _positive_pk(payload.get('revision_id'))
        if isinstance(payload.get('version'), bool) or not isinstance(payload.get('version'), int):
            raise ValidationError('نسخة مخطط غير صالحة.')
        with transaction.atomic():
            if payload.get('target') == 'template':
                instance = get_object_or_404(
                    VenueLayoutTemplate.objects.select_for_update(),
                    pk=pk, archived=False,
                )
                revision = get_object_or_404(VenueLayoutRevision, pk=revision_id, template=instance)
            elif payload.get('target') == 'event':
                instance = get_object_or_404(
                    EventLayoutSnapshot.objects.select_for_update(), event_id=pk,
                )
                revision = get_object_or_404(VenueLayoutRevision, pk=revision_id, event_layout=instance)
            else:
                raise ValidationError('هدف الاستعادة غير صالح.')
            if instance.version != payload['version']:
                return JsonResponse({'error': 'تغيّر المخطط. أعد فتحه أولاً.'}, status=409)
            instance.layout = validate_draft_layout(revision.layout)
            instance.version += 1
            instance.updated_by = request.user
            instance.save(update_fields=['layout', 'version', 'updated_by', 'updated_at'])
            VenueLayoutRevision.objects.create(
                template=instance if payload['target'] == 'template' else None,
                event_layout=instance if payload['target'] == 'event' else None,
                version=instance.version, layout=copy.deepcopy(instance.layout),
                created_by=request.user,
            )
            ActivityLog.objects.create(
                actor=request.user, action='venue_layout.revision_restored',
                details={'target': payload['target'], 'record_id': pk,
                         'from_revision_id': revision_id, 'new_version': instance.version},
            )
        return JsonResponse({'version': instance.version, 'restored': True})
    except (ValidationError, ValueError, TypeError) as error:
        msg = ' '.join(error.messages) if isinstance(error, ValidationError) else 'طلب الاستعادة غير صالح.'
        return JsonResponse({'error': msg}, status=400)
