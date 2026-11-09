"""Release A venue layout integration: isolated draft state, no public seat sales."""
import copy
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.layout_validation import validate_draft_layout
from events.models import Event, EventLayoutSnapshot, VenueLayoutTemplate
from reservations.models import Reservation


def example_layout():
    return {
        'version': '0.2', 'status': 'SCHEMATIC_NOT_APPROVED',
        'units': 'meters', 'layout': 'balanced', 'measured': False,
        'architecture': {
            'width': 7, 'height': 10, 'table': 0.5,
            'chairW': 0.43, 'chairD': 0.46, 'voidDepth': 4.1,
            'stage': {'x': 2.5, 'y': 0, 'w': 2, 'h': 1.2},
            'stairGround': {'x': 5.75, 'y': 1.85, 'w': 1.25, 'h': 3.30},
            'stairMezz': {'x': 5.75, 'y': 4.6, 'w': 1.25, 'h': 3.95},
            'aisle': {'x': 0.32, 'y': 2.35, 'w': 1.1, 'h': 6.25, 'mode': 'left'},
        },
        'objects': [
            {'id': 't001', 'kind': 'table', 'floor': 'ground', 'zone': 'premium',
             'x': 2.5, 'y': 2.75, 'rot': 90, 'seats': 3, 'locked': False,
             'seat_ids': ['t001-A', 't001-B', 't001-C']},
            {'id': 'c002', 'kind': 'chair', 'floor': 'mezz', 'zone': 'standard',
             'x': 2.5, 'y': 5.0, 'rot': 0, 'seats': 1, 'locked': False},
        ],
        'seats_proposed': {'ground': 3, 'mezz': 1, 'premium': 3, 'standard': 1, 'tables': 1},
        'warnings': [],
    }


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class VenueLayoutReleaseATests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='venue-layout-admin', phone='984422', password='test', role='admin',
        )
        self.kitchen = User.objects.create_user(
            username='venue-layout-kitchen', phone='984423', password='test', role='kitchen',
        )
        self.event = Event.objects.create(
            title_ar='ليلة اختبار المقاعد',
            starts_at=timezone.now() + timedelta(days=5),
            status=Event.Status.DRAFT, capacity=20, is_public=False,
        )
        self.new_url = reverse('staff_layout_new')
        self.save_url = reverse('staff_layout_save')

    def save(self, *, target='new', pk=None, version=0, data=None, name='كوميديا مرنة'):
        return self.client.post(self.save_url,
            data=json.dumps({
                'target': target, 'id': pk, 'version': version,
                'name': name, 'kind': 'comedy',
                'layout': data if data is not None else example_layout(),
            }), content_type='application/json')

    def test_layout_editor_is_staff_only_and_not_public(self):
        self.assertEqual(self.client.get(reverse('staff_layouts')).status_code, 302)
        self.assertEqual(self.client.get(self.new_url).status_code, 302)
        self.client.force_login(self.kitchen)
        self.assertNotEqual(self.client.get(self.new_url).status_code, 200)
        self.client.force_login(self.admin)
        response = self.client.get(self.new_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'HubLayoutLab')
        self.assertContains(response, 'serverSave')
        self.assertContains(response, 'serverLayoutKind')
        self.assertContains(response, '50 × 50')
        self.assertEqual(self.client.get(reverse('staff_layouts')).status_code, 200)

    def test_five_unapproved_event_type_templates_seeded(self):
        expected = {'comedy', 'theatre', 'cabaret', 'workshop', 'standing'}
        models = VenueLayoutTemplate.objects.filter(kind__in=expected)
        self.assertEqual(models.count(), 5)
        for item in models:
            self.assertFalse(item.layout['measured'])
            self.assertEqual(item.layout['status'], 'SCHEMATIC_NOT_APPROVED')
            self.assertEqual(validate_draft_layout(item.layout)['architecture']['width'], 7)

    def test_validate_legacy_prototype_document_normalizes_seats(self):
        doc = validate_draft_layout(example_layout())
        self.assertEqual(doc['seats_proposed']['ground'], 3)
        self.assertEqual(doc['seats_proposed']['mezz'], 1)
        self.assertNotIn('seat_ids', doc['objects'][0])
        self.assertFalse(doc['measured'])

    def test_save_and_reload_template_with_version_conflict(self):
        self.client.force_login(self.admin)
        created = self.save()
        self.assertEqual(created.status_code, 200, created.content)
        entry = VenueLayoutTemplate.objects.get(pk=created.json()['id'])
        self.assertEqual(entry.version, 1)
        self.assertEqual(entry.layout['seats_proposed']['premium'], 3)
        edit_url = reverse('staff_layout_edit', args=[entry.pk])
        page = self.client.get(edit_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 't001')
        changed = copy.deepcopy(example_layout())
        changed['objects'][0]['seats'] = 4
        saved = self.save(target='template', pk=entry.pk, version=1, data=changed)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()['version'], 2)
        stale = self.save(target='template', pk=entry.pk, version=1)
        self.assertEqual(stale.status_code, 409)
        entry.refresh_from_db()
        self.assertEqual(entry.layout['seats_proposed']['ground'], 4)

    def test_assign_snapshot_and_edit_event_independent_of_template(self):
        self.client.force_login(self.admin)
        template_id = self.save().json()['id']
        assign = self.client.post(reverse('staff_layout_assign'), {
            'event_id': self.event.pk, 'template_id': template_id,
        })
        self.assertEqual(assign.status_code, 302)
        snapshot = EventLayoutSnapshot.objects.get(event=self.event)
        self.assertEqual(snapshot.layout['seats_proposed']['premium'], 3)
        self.assertEqual(snapshot.version, 1)
        again = self.client.post(reverse('staff_layout_assign'), {
            'event_id': self.event.pk, 'template_id': template_id,
        })
        self.assertEqual(again.status_code, 302)
        self.assertEqual(EventLayoutSnapshot.objects.count(), 1)

        template = VenueLayoutTemplate.objects.get(pk=template_id)
        modified = example_layout()
        modified['objects'][0]['seats'] = 4
        self.assertEqual(self.save(target='template', pk=template_id, version=1, data=modified).status_code, 200)
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.layout['objects'][0]['seats'], 3)
        event_edit = self.save(target='event', pk=self.event.pk, version=1, data=modified, name=self.event.title_ar)
        self.assertEqual(event_edit.status_code, 200)
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.layout['objects'][0]['seats'], 4)
        template.refresh_from_db()
        self.assertEqual(template.layout['objects'][0]['seats'], 4)
        self.event.refresh_from_db()
        self.assertEqual(self.event.capacity, 20)
        self.assertEqual(self.event.status, Event.Status.DRAFT)
        self.assertFalse(Reservation.objects.exists())
        self.assertEqual(self.client.get(reverse('staff_event_layout_edit', args=[self.event.pk])).status_code, 200)
        self.assertContains(self.client.get(reverse('staff_event_detail', args=[self.event.pk])), 'مخطط المقاعد')

    def test_invalid_payloads_never_create_template_or_approved_capacity(self):
        self.client.force_login(self.admin)
        before = VenueLayoutTemplate.objects.count()
        unsafe = example_layout()
        unsafe['measured'] = True
        self.assertEqual(self.save(data=unsafe).status_code, 400)
        unsafe = example_layout()
        unsafe['architecture']['stage']['y'] = 1
        self.assertEqual(self.save(data=unsafe).status_code, 400)
        unsafe = example_layout()
        unsafe['objects'].append(copy.deepcopy(unsafe['objects'][0]))
        self.assertEqual(self.save(data=unsafe).status_code, 400)
        self.assertEqual(VenueLayoutTemplate.objects.count(), before)

    def test_read_only_public_events_have_no_seat_selector(self):
        self.client.force_login(self.admin)
        template_id = self.save().json()['id']
        self.client.post(reverse('staff_layout_assign'), {
            'event_id': self.event.pk, 'template_id': template_id,
        })
        self.assertEqual(Reservation.objects.count(), 0)
        self.assertFalse(hasattr(self.event, 'seat_holds'))
