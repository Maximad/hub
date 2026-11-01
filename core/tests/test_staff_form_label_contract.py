"""Critical staff creation controls have persistent accessible labels."""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class StaffFormLabelContractTests(SimpleTestCase):
    def _markup(self, name):
        return Path(settings.BASE_DIR, 'templates', 'staff', name).read_text(encoding='utf-8')

    def test_reservation_date_times_contact_and_party_size_have_persistent_labels(self):
        source = self._markup('reservation_form.html')
        for field_id in ('reservation-name', 'reservation-phone', 'reservation-date',
                         'reservation-start-time', 'reservation-end-time',
                         'reservation-status', 'reservation-party-size', 'reservation-notes'):
            with self.subTest(field=field_id):
                self.assertIn(f'for="{field_id}"', source)
                self.assertIn(f'id="{field_id}"', source)
        self.assertIn('id="reservation-phone" name="phone" type="tel"', source)
        self.assertIn('aria-label="موعد الحجز العادي"', source)

    def test_event_inputs_have_persistent_labels(self):
        source = self._markup('event_form.html')
        for field_id in ('event-title-ar', 'event-title-en', 'event-start',
                         'event-end', 'event-description', 'event-capacity', 'event-status'):
            with self.subTest(field=field_id):
                self.assertIn(f'for="{field_id}"', source)
                self.assertIn(f'id="{field_id}"', source)

    def test_member_inputs_have_persistent_labels_and_telephone_type(self):
        source = self._markup('member_form.html')
        for field_id in ('member-name', 'member-phone', 'member-default-plan', 'member-notes'):
            with self.subTest(field=field_id):
                self.assertIn(f'for="{field_id}"', source)
                self.assertIn(f'id="{field_id}"', source)
        self.assertIn('id="member-phone" type="tel"', source)
