from datetime import datetime
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase, override_settings

from operations.services import current_business_date


@override_settings(BUSINESS_DAY_CUTOFF_HOUR=4)
class BusinessDayCutoffTests(SimpleTestCase):
    def test_before_cutoff_belongs_to_previous_business_date(self):
        at = datetime(2026, 9, 23, 3, 30, tzinfo=ZoneInfo('Asia/Damascus'))
        self.assertEqual(current_business_date(at).isoformat(), '2026-09-22')

    def test_at_cutoff_starts_new_business_date(self):
        at = datetime(2026, 9, 23, 4, 0, tzinfo=ZoneInfo('Asia/Damascus'))
        self.assertEqual(current_business_date(at).isoformat(), '2026-09-23')
