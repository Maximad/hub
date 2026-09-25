from datetime import datetime, time, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import HubVisit
from core.templatetags.hub_time import ar_date, ar_datetime, ar_since
from operations.services import current_business_date


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    BUSINESS_DAY_CUTOFF_HOUR=4,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class FinalOperationsUxTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='final-ops-admin',
            password='pass',
            phone='+963955400001',
            role=User.Role.ADMIN,
        )
        self.client.force_login(self.admin)

    def _business_day_start(self):
        business_date = current_business_date()
        return timezone.make_aware(
            datetime.combine(business_date, time(hour=settings.BUSINESS_DAY_CUTOFF_HOUR)),
            timezone=timezone.get_current_timezone(),
        )

    def test_staff_home_separates_stale_visit_from_live_floor(self):
        start = self._business_day_start()
        stale = HubVisit.objects.create(notes='stale-account-marker')
        live = HubVisit.objects.create(notes='live-account-marker')
        HubVisit.objects.filter(pk=stale.pk).update(last_activity_at=start - timedelta(hours=2))
        HubVisit.objects.filter(pk=live.pk).update(last_activity_at=start + timedelta(hours=2))

        response = self.client.get(reverse('staff_home'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['workspace_stats']['open_visits'], 1)
        self.assertEqual(response.context['workspace_stats']['stale_visits'], 1)
        self.assertEqual(response.context['visit_rows'][0]['visit'].pk, live.pk)
        self.assertEqual(response.context['stale_visit_rows'][0]['visit'].pk, stale.pk)
        self.assertContains(response, 'حساباً قديماً بحاجة للمراجعة')
        self.assertContains(response, 'لم تُغلق تلقائياً')

    def test_arabic_date_and_elapsed_filters_do_not_emit_english_units(self):
        value = timezone.make_aware(
            datetime(2026, 9, 22, 15, 43),
            timezone=timezone.get_current_timezone(),
        )
        later = value + timedelta(hours=8, minutes=10)

        self.assertEqual(ar_date(value), '22 أيلول 2026')
        self.assertEqual(ar_datetime(value), '22 أيلول 2026 · 15:43')
        elapsed = ar_since(value, later)
        self.assertEqual(elapsed, '8 ساعات')
        self.assertNotIn('hour', elapsed.lower())
        self.assertNotIn('minute', elapsed.lower())
