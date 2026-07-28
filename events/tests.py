from datetime import datetime
from django.test import TestCase
from django.utils import timezone
from core.models import Room
from .models import Event


class EventRoomTests(TestCase):
    def test_event_uses_room_without_table(self):
        room = Room.objects.create(name_ar='الاستوديو')
        event = Event.objects.create(title_ar='فعالية', starts_at=timezone.make_aware(datetime(2026, 7, 30, 20)), room=room)
        self.assertEqual(event.room, room)
        self.assertFalse(hasattr(event, 'location_area'))
