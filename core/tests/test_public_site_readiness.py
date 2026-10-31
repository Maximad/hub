import json
from datetime import datetime, timedelta, timezone as dt_timezone
from io import StringIO
from unittest.mock import PropertyMock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import AccessProfile, UserCapabilityOverride
from catalog.models import MediaAsset
from core.models import HomepageFeature
from core.services.public_site_readiness import public_site_readiness_report
from events.models import Event, EventMedia
from members.models import MembershipBenefitRule, MembershipPlan, Program
from reservations.models import EventAttendanceRequest as Request
from vendors.models import Vendor, VendorMedia, VendorParticipation


NOW = datetime(2026, 10, 31, 12, tzinfo=dt_timezone.utc)


@override_settings(PUBLIC_HOMEPAGE_ENABLED=True)
class PublicSiteReadinessTests(TestCase):
    def setUp(self):
        clock = patch('django.utils.timezone.now', return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def report(self, **kwargs):
        return public_site_readiness_report(now=NOW, **kwargs)

    def check(self, report, code):
        return next(check for check in report['checks'] if check['code'] == code)

    def event(self, **kwargs):
        values = dict(title_ar='PRIVATE_EVENT_TITLE', status='published', is_public=True,
                      starts_at=NOW + timedelta(days=1), capacity=10)
        values.update(kwargs)
        return Event.objects.create(**values)

    def request(self, event=None, **kwargs):
        values = dict(event=event, event_uuid=event.uuid if event else uuid4(),
                      event_title='PRIVATE_SNAPSHOT', name='PRIVATE_CONTACT', phone='PRIVATE_PHONE',
                      owner_hash=uuid4().hex * 2, ip_hash='PRIVATE_IP_HASH', client_key=uuid4(),
                      review_reason='PRIVATE_REASON', party_size=2)
        values.update(kwargs)
        return Request.objects.create(**values)

    def plan(self, code='public-plan', **kwargs):
        values = dict(code=code, name_ar='PRIVATE_PLAN_NAME', is_active=True,
                      visible_to_customer=True, price_syp=15000)
        values.update(kwargs)
        return MembershipPlan.objects.create(**values)

    def popup(self, **kwargs):
        vendor = Vendor.objects.create(name_ar='PRIVATE_VENDOR', phone='PRIVATE_VENDOR_PHONE',
                                       is_public=True, is_active=True)
        values = dict(vendor=vendor, title_ar='PRIVATE_POPUP', is_public=True,
                      starts_at=NOW + timedelta(days=1), status='active')
        values.update(kwargs)
        return VendorParticipation.objects.create(**values)

    def user(self, **kwargs):
        key = uuid4().hex
        values = dict(username='PRIVATE_USER_' + key, phone=key[:30], role='staff')
        values.update(kwargs)
        return get_user_model().objects.create_user(**values)

    def test_empty_optional_sections_are_supported_and_read_only(self):
        with CaptureQueriesContext(connection) as queries:
            report = self.report()
        self.assertEqual(report['status'], 'PASS')
        self.assertTrue(report['read_only'])
        self.assertEqual(report['metrics']['events']['total'], 0)
        self.assertEqual(report['metrics']['feature']['rendered_source'], 'evergreen_fallback')
        self.assertNotIn('reviewers', report['metrics'])
        self.assertFalse(any(query['sql'].lstrip().split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE'}
                             for query in queries))

    @override_settings(PUBLIC_HOMEPAGE_ENABLED=False)
    def test_disabled_release_is_reported_without_being_enabled(self):
        report = self.report()
        self.assertFalse(report['release_enabled'])
        self.assertEqual(self.check(report, 'release_gate')['status'], 'WARN')
        self.assertIn('current_command_settings', report['scope'])
        self.assertIn('No deployed revision', ' '.join(report['limitations']))

    def test_counts_reuse_publication_gates_and_date_boundaries(self):
        self.event()
        self.event(starts_at=NOW - timedelta(hours=1), ends_at=NOW + timedelta(hours=1))
        self.event(is_public=False)
        self.event(status='draft')
        self.event(starts_at=NOW - timedelta(hours=1), ends_at=NOW)
        Program.objects.create(code='open', name_ar='PRIVATE_PROGRAM', is_public=True, status='open')
        Program.objects.create(code='private', name_ar='PRIVATE_PROGRAM', is_public=False, status='open')
        self.popup()
        self.popup(is_public=False)
        report = self.report()
        self.assertEqual(report['metrics']['events']['total'], 2)
        self.assertEqual(report['metrics']['programs']['total'], 1)
        self.assertEqual(report['metrics']['popups']['total'], 1)

    def test_feature_rechecks_source_and_never_outputs_private_override(self):
        self.event()
        private = self.event(is_public=False)
        HomepageFeature.objects.create(source_kind='event', event=private, enabled=True, is_published=True,
                                       summary_ar='PRIVATE_EDITORIAL_COPY')
        report = self.report()
        self.assertEqual(report['metrics']['feature'], dict(reason='ineligible_source', rendered_source='event'))
        self.assertEqual(self.check(report, 'homepage_feature')['status'], 'WARN')
        self.assertNotIn('PRIVATE_', json.dumps(report))
        private.is_public = True
        private.save()
        self.assertEqual(self.report()['metrics']['feature']['reason'], 'selected')

    def test_disabled_or_outside_schedule_features_use_safe_fallback(self):
        feature = HomepageFeature.objects.create(enabled=False, is_published=True)
        self.assertEqual(self.report()['metrics']['feature']['reason'], 'disabled')
        for starts, ends in [(NOW + timedelta(seconds=1), None), (None, NOW)]:
            feature.enabled, feature.starts_at, feature.ends_at = True, starts, ends
            feature.save()
            report = self.report()
            self.assertEqual(report['metrics']['feature']['reason'], 'outside_schedule')
            self.assertEqual(self.check(report, 'homepage_feature')['status'], 'PASS')
        feature.starts_at, feature.ends_at = NOW, None
        feature.save()
        self.assertEqual(self.report()['metrics']['feature']['reason'], 'selected')

    def test_missing_media_warns_but_intentional_text_only_does_not(self):
        event = self.event()
        self.assertEqual(self.check(self.report(), 'public_media')['status'], 'PASS')
        asset = MediaAsset.objects.create(title_ar='PRIVATE_ARTWORK', file='missing/readiness-event.png')
        EventMedia.objects.create(event=event, media_asset=asset, role='cover')
        report = self.report()
        self.assertEqual(report['metrics']['media']['events_without_usable_configured_media'], 1)
        self.assertEqual(self.check(report, 'public_media')['status'], 'WARN')
        asset.external_url, asset.file = 'https://example.com/approved.png', ''
        asset.save()
        self.assertEqual(self.check(self.report(), 'public_media')['status'], 'PASS')

    def test_popup_media_only_checks_explicitly_public_artwork(self):
        popup = self.popup()
        asset = MediaAsset.objects.create(title_ar='PRIVATE_ARTWORK', external_url='javascript:invalid')
        link = VendorMedia.objects.create(vendor=popup.vendor, media_asset=asset, role='cover')
        self.assertEqual(self.check(self.report(), 'public_media')['status'], 'PASS')
        link.is_public = True
        link.save()
        self.assertEqual(self.report()['metrics']['media']['popups_without_usable_configured_media'], 1)

    def test_selected_feature_image_checked_but_draft_override_not_audited(self):
        asset = MediaAsset.objects.create(title_ar='PRIVATE_ARTWORK', file='missing/readiness-feature.png')
        feature = HomepageFeature.objects.create(enabled=False, is_published=True, image=asset)
        self.assertFalse(self.report()['metrics']['media']['selected_feature_image_unusable'])
        feature.enabled = True
        feature.save()
        report = self.report()
        self.assertTrue(report['metrics']['media']['selected_feature_image_unusable'])
        self.assertEqual(self.check(report, 'public_media')['status'], 'WARN')
        self.assertNotIn('PRIVATE_', json.dumps(report))

    def test_no_derivatives_generated_or_files_saved(self):
        event = self.event()
        asset = MediaAsset.objects.create(title_ar='asset', external_url='https://example.com/asset.png')
        EventMedia.objects.create(event=event, media_asset=asset, role='cover')
        with patch.object(MediaAsset, 'thumbnail_url', new_callable=PropertyMock,
                          side_effect=AssertionError('No derivatives')), \
                patch('django.core.files.storage.FileSystemStorage.save', side_effect=AssertionError('No file writes')):
            self.assertEqual(self.check(self.report(), 'public_media')['status'], 'PASS')

    def test_membership_readiness_uses_commercial_preflight_and_aggregates_only(self):
        self.plan('ready')
        blocked = self.plan('blocked', notes='PRIVATE_PLAN_NOTES', metadata={'secret': 'PRIVATE_METADATA'})
        MembershipBenefitRule.objects.create(plan=blocked, benefit_type='internet_minutes', included_minutes=0)
        hidden = self.plan('hidden', visible_to_customer=False)
        MembershipBenefitRule.objects.create(plan=hidden, benefit_type='internet_minutes', included_minutes=0)
        advisory = self.plan('advisory')
        MembershipBenefitRule.objects.create(plan=advisory, benefit_type='booking_priority', notes='PRIVATE_BENEFIT')
        report = self.report()
        self.assertEqual(report['metrics']['memberships'],
                         dict(total=3, inspected=3, truncated=False, blocked=1, advisory=2))
        self.assertEqual(self.check(report, 'membership_configuration')['status'], 'WARN')
        self.assertNotIn('PRIVATE_', json.dumps(report))

    def test_sampling_is_bounded_and_incomplete_coverage_is_explicit(self):
        for n in range(3):
            self.event()
            self.popup()
            self.plan(str(n))
        with patch('core.services.public_site_readiness.assess_membership_plan',
                   return_value=dict(can_sell=True, warnings=[])) as assess:
            report = self.report(limit=2)
        self.assertEqual(assess.call_count, 2)
        for section in ['events', 'popups', 'memberships']:
            self.assertEqual(report['metrics'][section]['inspected'], 2)
            self.assertTrue(report['metrics'][section]['truncated'])
            self.assertEqual(self.check(report, section + '_coverage')['status'], 'WARN')

    def test_future_opt_ins_are_separate_from_global_gate_and_old_events(self):
        self.event(accepts_attendance_requests=True)
        self.event(accepts_attendance_requests=True, is_public=False)
        self.event(accepts_attendance_requests=True, status='draft')
        self.event(accepts_attendance_requests=True, starts_at=NOW)
        self.event(accepts_attendance_requests=True, starts_at=NOW - timedelta(hours=1))
        with override_settings(PUBLIC_HOMEPAGE_ENABLED=False):
            report = self.report()
        self.assertEqual(report['metrics']['intake'],
                         dict(eligible_events=1, future_opted_in_events=3, future_opted_in_but_closed=2))
        self.assertEqual(self.check(report, 'attendance_opt_ins')['status'], 'WARN')

    def test_historical_opt_ins_do_not_warn_forever(self):
        self.event(accepts_attendance_requests=True, starts_at=NOW)
        self.assertEqual(self.check(self.report(), 'attendance_opt_ins')['status'], 'PASS')

    def test_pending_requests_flag_deleted_private_closed_and_stale_events(self):
        accepting = self.event(accepts_attendance_requests=True)
        stale = self.request(accepting)
        Request.objects.filter(pk=stale.pk).update(created_at=NOW - timedelta(hours=24, seconds=1))
        self.request(self.event(is_public=False, accepts_attendance_requests=True))
        self.request(self.event())
        deleted = self.event(accepts_attendance_requests=True)
        self.request(deleted)
        deleted.delete()
        self.request(accepting, status='rejected')
        self.request(status='approved')
        report = self.report()
        self.assertEqual(report['metrics']['requests'], dict(pending=4, older_than_24h=1,
                         event_not_accepting=3, approved_without_reservation=1))
        self.assertEqual(self.check(report, 'attendance_queue')['status'], 'WARN')
        self.assertEqual(Request.objects.get(pk=stale.pk).status, 'pending')
        self.assertNotIn('PRIVATE_', json.dumps(report))

    def test_recent_pending_is_information_not_automatic_warning_or_expiry(self):
        event = self.event(accepts_attendance_requests=True)
        row = self.request(event)
        Request.objects.filter(pk=row.pk).update(created_at=NOW - timedelta(hours=24))
        self.user(is_superuser=True)
        self.assertEqual(self.check(self.report(), 'attendance_queue')['status'], 'PASS')
        self.assertEqual(Request.objects.get(pk=row.pk).status, 'pending')

    def test_reviewers_use_profiles_overrides_and_active_status(self):
        self.event(accepts_attendance_requests=True)
        profile = AccessProfile.objects.create(code='readiness', name_ar='PRIVATE_PROFILE',
                                               base_role='staff', capabilities=['reservations'])
        denied = self.user(access_profile=profile)
        UserCapabilityOverride.objects.create(user=denied, capability='reservations', allowed=False)
        granted = self.user(role='staff')
        UserCapabilityOverride.objects.create(user=granted, capability='reservations', allowed=True)
        self.user(is_active=False, is_superuser=True)
        report = self.report()
        self.assertEqual(report['metrics']['reviewers']['authorized_in_sample'], 1)
        self.assertEqual(self.check(report, 'attendance_reviewers')['status'], 'PASS')
        UserCapabilityOverride.objects.filter(user=granted).update(allowed=False)
        self.assertEqual(self.check(self.report(), 'attendance_reviewers')['status'], 'WARN')

    def test_reviewers_outside_sample_do_not_claim_complete_coverage(self):
        self.event(accepts_attendance_requests=True)
        self.user(role='staff')
        self.user(is_superuser=True)
        report = self.report(limit=1)
        self.assertTrue(report['metrics']['reviewers']['truncated'])
        self.assertEqual(self.check(report, 'reviewers_coverage')['status'], 'WARN')
        self.assertEqual(report['metrics']['reviewers']['authorized_in_sample'], 0)

    def test_populated_report_performs_no_database_writes(self):
        event = self.event(accepts_attendance_requests=True)
        self.request(event)
        self.plan()
        self.popup()
        self.user(is_superuser=True)
        HomepageFeature.objects.create(source_kind='event', event=event, enabled=True, is_published=True)
        with CaptureQueriesContext(connection) as queries:
            self.report()
        self.assertFalse(any(query['sql'].lstrip().split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE'}
                             for query in queries))

    def test_report_uses_damascus_independent_of_active_timezone(self):
        with timezone.override('America/New_York'):
            report = self.report()
        self.assertEqual(report['checked_at'], '2026-10-31T15:00:00+03:00')
        self.assertEqual(report['timezone'], 'Asia/Damascus')

    def test_limit_validation(self):
        for limit in [0, 501, -1, True, '10', None]:
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.report(limit=limit)

    def test_command_json_text_and_strict_success(self):
        stream = StringIO()
        call_command('public_site_readiness', '--json', '--strict', stdout=stream)
        self.assertEqual(json.loads(stream.getvalue())['status'], 'PASS')
        stream = StringIO()
        call_command('public_site_readiness', stdout=stream)
        self.assertIn('current command environment', stream.getvalue())
        self.assertIn('Result: PASS', stream.getvalue())

    @override_settings(PUBLIC_HOMEPAGE_ENABLED=False)
    def test_command_warns_normally_but_strict_fails_after_writing_report(self):
        stream = StringIO()
        call_command('public_site_readiness', '--json', stdout=stream)
        self.assertEqual(json.loads(stream.getvalue())['status'], 'WARN')
        stream = StringIO()
        with self.assertRaises(CommandError):
            call_command('public_site_readiness', '--json', '--strict', stdout=stream)
        self.assertEqual(json.loads(stream.getvalue())['status'], 'WARN')

    def test_invalid_command_limit_errors_before_inspection(self):
        for limit in [0, 501]:
            with self.subTest(limit=limit), patch('core.management.commands.public_site_readiness.public_site_readiness_report') as report:
                with self.assertRaises(CommandError):
                    call_command('public_site_readiness', limit=limit, stdout=StringIO())
                report.assert_not_called()
