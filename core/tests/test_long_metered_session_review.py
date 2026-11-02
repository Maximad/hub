"""Exceptional metered session age only creates a review affordance, never a charge."""
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import InternetSession


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class LongMeteredSessionReviewTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username='long-session-review-admin', phone='+963998001206', role='admin',
        )
        self.client.force_login(user)

    def test_long_session_is_flagged_read_only_and_short_session_not_flagged(self):
        now = timezone.now()
        old = InternetSession.objects.create(
            started_at=now - timedelta(days=2),
            start_time=now - timedelta(days=2),
            billing_mode=InternetSession.BillingMode.OPEN_METERED,
            status=InternetSession.Status.ACTIVE,
            rate_per_hour_syp=600,
        )
        recent = InternetSession.objects.create(
            started_at=now - timedelta(minutes=40),
            start_time=now - timedelta(minutes=40),
            billing_mode=InternetSession.BillingMode.OPEN_METERED,
            status=InternetSession.Status.ACTIVE,
            rate_per_hour_syp=600,
        )
        with patch('django.utils.timezone.now', return_value=now):
            response = self.client.get(reverse('staff_internet'))
        self.assertEqual(response.status_code, 200)
        rows = {r['session'].pk: r for r in response.context['active_rows']}
        self.assertTrue(rows[old.pk]['review_long'])
        self.assertFalse(rows[recent.pk]['review_long'])
        self.assertContains(response, 'مراجعة بشرية · مدة غير اعتيادية', count=1)
        self.assertContains(response, reverse('staff_internet_session', args=[old.pk]))
        self.assertEqual(InternetSession.objects.get(pk=old.pk).status, InternetSession.Status.ACTIVE)
        self.assertIsNone(InternetSession.objects.get(pk=old.pk).linked_order_id)

    def test_detail_explains_network_limit_without_automatic_finalization(self):
        source = Path(settings.BASE_DIR, 'templates', 'staff', 'internet_session.html').read_text()
        self.assertIn('preview.duration >= 1440', source)
        self.assertIn('هذا التنبيه لا يوقف الاتصال', source)
        self.assertIn('سجل الشبكة', source)
