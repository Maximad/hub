"""No-key market-rate feed tests. All external requests are mocked or file based."""
import hashlib
import json
import tempfile
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.currency import applicable_rate
from core.models import ExchangeRate, ImportedMarketRate, LocalExchangeRatePolicy
from core.services.exchange_rate_feed import (
    EXPECTED_SOURCE_URL, approve_candidate, parse_unapproved_record, stage_unapproved_rate,
)
from scripts.collect_lirascope_rates import RateFeedError, validate_payload


def example_document(*, now=None, buy='135', sell='137'):
    now = now or timezone.now()
    at = now - timedelta(minutes=5)
    return {
        'schema_version': 1, 'provider': 'lirascope', 'reference_market': 'marketRates',
        'city': 'unspecified', 'currency': 'USD', 'denomination': 'SYP_NEW',
        'denomination_verification': 'numeric_safety_bounds_and_manual_approval',
        'buy_syp_per_usd': str(buy), 'sell_syp_per_usd': str(sell),
        'source_timestamp_utc': at.isoformat().replace('+00:00', 'Z'),
        'collected_at_utc': now.isoformat().replace('+00:00', 'Z'),
        'business_date_damascus': at.astimezone(ZoneInfo('Asia/Damascus')).date().isoformat(),
        'source_url': EXPECTED_SOURCE_URL, 'raw_sha256': 'a' * 64, 'status': 'unapproved',
    }


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class ExchangeRateFeedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_superuser(
            username='rate-owner', phone='981131', password='LongTestPassword#1'
        )
        cls.manager = User.objects.create_user(
            username='rate-manager', phone='981132', password='LongTestPassword#1',
            role='admin', is_staff=True,
        )

    def test_collector_market_rates_only_and_no_key(self):
        now = datetime(2026, 10, 17, 15, tzinfo=dt_timezone.utc)
        at = (now - timedelta(minutes=10)).isoformat().replace('+00:00', 'Z')
        source = {
            'timestampUtc': at,
            'cbsRates': [{'currency': 'USD', 'buy': 110, 'sell': 111, 'timestampUtc': at}],
            'marketRates': [{'currency': 'USD', 'buy': '135.00', 'sell': '136.00',
                             'timestampUtc': at, 'isManualOverride': False}],
        }
        raw = json.dumps(source).encode()
        result = validate_payload(source, raw, now=now)
        self.assertEqual(result['reference_market'], 'marketRates')
        self.assertEqual(result['buy_syp_per_usd'], '135.00')
        self.assertEqual(result['status'], 'unapproved')
        self.assertEqual(result['denomination'], 'SYP_NEW')
        self.assertEqual(result['city'], 'unspecified')
        self.assertEqual(result['raw_sha256'], hashlib.sha256(raw).hexdigest())
        with self.assertRaises(RateFeedError):
            validate_payload({**source, 'marketRates': source['cbsRates'][:0]}, raw, now=now)
        with self.assertRaises(RateFeedError):
            validate_payload({**source, 'marketRates': [{**source['marketRates'][0], 'buy': 13500}]}, raw, now=now)
        with self.assertRaises(RateFeedError):
            validate_payload({**source, 'marketRates': [{**source['marketRates'][0], 'timestampUtc':
                (now - timedelta(days=4)).isoformat()}]}, raw, now=now)
        with self.assertRaises(RateFeedError):
            validate_payload({**source, 'meta': {'denomination': 'old'}}, raw, now=now)

    def test_stage_file_is_idempotent_and_does_not_approve_or_post(self):
        doc = example_document()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'latest.json'
            path.write_text(json.dumps(doc), encoding='utf8')
            call_command('stage_exchange_rate_feed', '--file', str(path), '--dry-run')
            self.assertEqual(ImportedMarketRate.objects.count(), 0)
            call_command('stage_exchange_rate_feed', '--file', str(path))
            call_command('stage_exchange_rate_feed', '--file', str(path))
        self.assertEqual(ImportedMarketRate.objects.count(), 1)
        row = ImportedMarketRate.objects.get()
        self.assertEqual(row.status, 'pending')
        self.assertIsNone(row.approved_exchange_rate)
        self.assertFalse(ExchangeRate.objects.filter(source__startswith='LiraScope').exists())

    def test_stage_rejects_spoofed_currency_provider_and_stale_records(self):
        for field, bad in (('source_url', 'https://example.com/'),
                           ('denomination', 'OLD_SYP'),
                           ('provider', 'untrusted'),
                           ('status', 'approved')):
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    parse_unapproved_record({**example_document(), field: bad})
        with self.assertRaises(ValidationError):
            parse_unapproved_record(example_document(now=timezone.now() - timedelta(days=4)))
        self.assertEqual(ImportedMarketRate.objects.count(), 0)

    def test_only_owner_can_view_settings_and_approve(self):
        row, _ = stage_unapproved_rate(example_document())
        self.client.force_login(self.manager)
        response = self.client.get(reverse('staff_exchange_rate_settings'))
        self.assertEqual(response.status_code, 403)
        denied = self.client.post(reverse('staff_exchange_rate_settings'), {
            'action': 'approve', 'candidate_id': row.pk,
        })
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(row.status, 'pending')
        self.client.force_login(self.owner)
        response = self.client.get(reverse('staff_exchange_rate_settings'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'فرق سعر السويداء')
        self.assertContains(self.client.get(reverse('staff_home')), reverse('staff_exchange_rate_settings'))

    def test_signed_local_adjustment_and_approval_create_immutable_rate(self):
        row, _ = stage_unapproved_rate(example_document())
        self.client.force_login(self.owner)
        response = self.client.post(reverse('staff_exchange_rate_settings'), {
            'action': 'adjustment', 'sweida_offset_syp': '-1.50',
        })
        self.assertRedirects(response, reverse('staff_exchange_rate_settings'))
        self.assertEqual(LocalExchangeRatePolicy.objects.get(pk=1).sweida_offset_syp, Decimal('-1.50'))
        response = self.client.post(reverse('staff_exchange_rate_settings'), {
            'action': 'approve', 'candidate_id': row.pk,
        })
        self.assertRedirects(response, reverse('staff_exchange_rate_settings'))
        row.refresh_from_db()
        self.assertEqual(row.status, 'approved')
        self.assertEqual(row.applied_offset_syp, Decimal('-1.50'))
        rate = row.approved_exchange_rate
        self.assertEqual(rate.rate_to_base, Decimal('134.50'))
        self.assertEqual(rate.created_by, self.owner)
        self.assertEqual(rate.effective_date, row.business_date)
        # Current approved rate is visible to existing USD conversion boundary.
        self.assertEqual(applicable_rate(row.business_date).pk, rate.pk)
        before_count = ExchangeRate.objects.count()
        with self.assertRaises(ValidationError):
            approve_candidate(row.pk, actor=self.owner)
        self.assertEqual(ExchangeRate.objects.count(), before_count)
        self.client.post(reverse('staff_exchange_rate_settings'), {
            'action': 'adjustment', 'sweida_offset_syp': '2.00',
        })
        rate.refresh_from_db()
        self.assertEqual(rate.rate_to_base, Decimal('134.50'))

    def test_reject_does_not_publish_or_update_history(self):
        row, _ = stage_unapproved_rate(example_document())
        self.client.force_login(self.owner)
        before = ExchangeRate.objects.count()
        self.client.post(reverse('staff_exchange_rate_settings'), {
            'action': 'reject', 'candidate_id': row.pk, 'reason': 'اختلاف كبير مع سعر السوق',
        })
        row.refresh_from_db()
        self.assertEqual(row.status, 'rejected')
        self.assertIn('اختلاف', row.review_note)
        self.assertEqual(ExchangeRate.objects.count(), before)

    def test_offset_rejects_nan_and_out_of_bounds_without_mutation(self):
        self.client.force_login(self.owner)
        for value in ('NaN', 'Infinity', '500', '-51', 'abc'):
            with self.subTest(value=value):
                self.client.post(reverse('staff_exchange_rate_settings'), {
                    'action': 'adjustment', 'sweida_offset_syp': value,
                })
                self.assertFalse(LocalExchangeRatePolicy.objects.exists())

    def test_expired_rate_cannot_be_approved(self):
        doc = example_document(now=timezone.now() - timedelta(hours=35))
        row, _ = stage_unapproved_rate(doc)
        row.source_timestamp = timezone.now() - timedelta(days=4)
        row.save(update_fields=['source_timestamp'])
        self.client.force_login(self.owner)
        self.client.post(reverse('staff_exchange_rate_settings'), {
            'action': 'approve', 'candidate_id': row.pk,
        })
        row.refresh_from_db()
        self.assertEqual(row.status, 'pending')
        self.assertIsNone(row.approved_exchange_rate)

    def test_view_as_does_not_let_owner_approve_or_reveal_settings(self):
        from accounts.middleware import VIEW_AS_USER_SESSION_KEY
        self.client.force_login(self.owner)
        session = self.client.session
        session[VIEW_AS_USER_SESSION_KEY] = self.manager.pk
        session.save()
        self.assertEqual(self.client.get(reverse('staff_exchange_rate_settings')).status_code, 403)
        self.assertEqual(self.client.post(reverse('staff_exchange_rate_settings'), {
            'action': 'adjustment', 'sweida_offset_syp': '1',
        }).status_code, 403)
