from dataclasses import asdict
from datetime import datetime, timedelta, timezone as dt_timezone
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.admin.models import LogEntry
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from catalog.models import MediaAsset
from core.models import ActivityLog, HomepageFeature
from core.services.public_homepage import public_homepage_context
from core.settings_helpers import get_system_settings
from events.models import Event, EventMedia
from members.models import Program
from vendors.models import Vendor, VendorParticipation

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=dt_timezone.utc)


@override_settings(PUBLIC_HOMEPAGE_ENABLED=True, ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class HomepageFeatureTests(TestCase):
    def setUp(self):
        clock = patch('django.utils.timezone.now', return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        get_system_settings.cache_clear()
        self.addCleanup(get_system_settings.cache_clear)

    def event(self, title='لقاء قريب', **kwargs):
        return Event.objects.create(**dict(dict(title_ar=title, status='published', is_public=True,
                                               starts_at=NOW+timedelta(hours=1)), **kwargs))

    def feature(self, **kwargs):
        return HomepageFeature.objects.create(**dict(dict(source_kind='evergreen', enabled=True, is_published=True), **kwargs))

    def hero(self, **kwargs):
        return public_homepage_context(now=NOW, **kwargs)['hero']

    def user(self, superuser=True, **kwargs):
        values = dict(username='editor', password='test', is_staff=True, is_superuser=superuser)
        values.update(kwargs)
        return get_user_model().objects.create_user(**values)

    def preview_url(self):
        return reverse('admin:core_homepagefeature_preview')

    def test_no_configuration_defaults_to_nearest_event_then_evergreen(self):
        self.assertEqual(HomepageFeature.objects.count(), 0)
        self.assertIsNone(self.hero())
        first = self.event()
        self.event('لاحق', starts_at=NOW+timedelta(days=1))
        self.assertEqual(self.hero().title, first.title_ar)
        first.delete()
        self.assertEqual(self.hero().title, 'لاحق')
        Event.objects.all().delete()
        self.assertIsNone(self.hero())
        self.assertContains(self.client.get('/'), 'أهلاً في هَبّ')

    def test_new_configuration_is_disabled_and_unpublished(self):
        feature = HomepageFeature.objects.create()
        self.assertFalse(feature.enabled)
        self.assertFalse(feature.is_published)
        self.assertIsNone(self.hero())

    def test_event_override_is_not_limited_to_first_three_cards(self):
        for index in range(3):
            self.event(str(index))
        later = self.event('اللقاء المختار', starts_at=NOW+timedelta(days=1))
        self.feature(source_kind='event', event=later, summary_ar='ملخص معتمد')
        context = public_homepage_context(now=NOW)
        self.assertEqual(context['hero'].title, later.title_ar)
        self.assertEqual(context['hero'].summary, 'ملخص معتمد')
        self.assertNotIn(later.title_ar, [e.title for e in context['events']])
        self.assertContains(self.client.get('/'), 'ملخص معتمد')

    def test_schedule_start_inclusive_end_exclusive(self):
        self.event('البديل', starts_at=NOW+timedelta(days=3))
        feature = self.feature(starts_at=NOW, ends_at=NOW+timedelta(hours=1), summary_ar='اختيار تحريري')
        self.assertEqual(self.hero().source_kind, 'evergreen')
        for at in [NOW-timedelta(microseconds=1), feature.ends_at, feature.ends_at+timedelta(days=1)]:
            hero = public_homepage_context(now=at)['hero']
            self.assertEqual(hero.title, 'البديل')
        feature.starts_at = None
        feature.ends_at = None
        feature.save()
        self.assertEqual(self.hero().source_kind, 'evergreen')

    def test_disabled_and_unpublished_overrides_never_leak_editorial_content(self):
        fallback = self.event()
        feature = self.feature(summary_ar='PRIVATE_SUMMARY')
        for field in ['enabled', 'is_published']:
            setattr(feature, field, False)
            feature.save()
            self.assertEqual(self.hero().title, fallback.title_ar)
            self.assertNotContains(self.client.get('/'), 'PRIVATE_SUMMARY')
            setattr(feature, field, True)

    def test_source_withdrawal_cancellation_expiry_and_deletion_revalidate(self):
        fallback = self.event('البديل')
        selected = self.event('المختار', starts_at=NOW+timedelta(hours=2))
        self.feature(source_kind='event', event=selected, summary_ar='OVERRIDE_COPY')
        self.assertEqual(self.hero().title, 'المختار')
        for change in [dict(status='draft'), dict(status='cancelled'), dict(status='completed'),
                       dict(status='published', is_public=False),
                       dict(is_public=True, starts_at=NOW-timedelta(seconds=1))]:
            Event.objects.filter(pk=selected.pk).update(**change)
            self.assertEqual(self.hero().title, fallback.title_ar)
            self.assertNotContains(self.client.get('/'), 'OVERRIDE_COPY')
        selected.delete()
        self.assertEqual(self.hero().title, fallback.title_ar)
        self.assertEqual(HomepageFeature.objects.get().source_kind, 'event')
        self.assertIsNone(HomepageFeature.objects.get().event_id)

    def test_ongoing_event_stops_promoting_at_end(self):
        event = self.event(starts_at=NOW-timedelta(hours=1), ends_at=NOW+timedelta(seconds=1))
        self.feature(source_kind='event', event=event)
        self.assertTrue(self.hero().is_ongoing)
        self.assertIsNone(public_homepage_context(now=event.ends_at)['hero'])

    def test_program_source_reuses_public_active_open_and_end_gates(self):
        self.event('البديل')
        program = Program.objects.create(code='program', name_ar='برنامج معلن', is_active=True, is_public=True, status='open')
        self.feature(source_kind='program', program=program)
        self.assertEqual(self.hero().url, '/programs/program/')
        self.assertNotContains(self.client.get('/'), 'على الموعد')
        for change in [dict(is_public=False), dict(is_public=True, is_active=False),
                       dict(is_active=True, status='cancelled'), dict(status='open', ends_at=NOW)]:
            Program.objects.filter(pk=program.pk).update(**change)
            self.assertEqual(self.hero().title, 'البديل')
        program.delete()
        self.assertEqual(self.hero().title, 'البديل')

    def test_participation_rechecks_vendor_and_participation_publication(self):
        self.event('البديل')
        vendor = Vendor.objects.create(name_ar='ضيف', is_active=True, is_public=True, phone='PRIVATE_PHONE')
        popup = VendorParticipation.objects.create(vendor=vendor, title_ar='تجربة', status='active', is_public=True,
                                                   starts_at=NOW+timedelta(days=1), notes='PRIVATE_NOTES')
        self.feature(source_kind='participation', participation=popup)
        self.assertEqual(self.hero().source_kind, 'participation')
        for obj, field, value in [(vendor, 'is_public', False), (vendor, 'is_active', False),
                                  (popup, 'is_public', False), (popup, 'status', 'planned')]:
            old = getattr(obj, field)
            setattr(obj, field, value)
            obj.save()
            self.assertEqual(self.hero().title, 'البديل')
            setattr(obj, field, old)
            obj.save()
        for private in ['PRIVATE_PHONE', 'PRIVATE_NOTES']:
            self.assertNotIn(private, str(asdict(self.hero())))
            self.assertNotContains(self.client.get('/'), private)
        vendor.delete()
        self.assertEqual(self.hero().title, 'البديل')

    def test_explicit_evergreen_override_uses_public_destination_without_fake_date(self):
        self.event()
        self.feature(summary_ar='تعريف معتمد بالمكان')
        hero = self.hero()
        self.assertEqual(hero.source_kind, 'evergreen')
        self.assertEqual(hero.url, '/events/')
        self.assertIsNone(hero.starts_at)
        self.assertContains(self.client.get('/'), 'تعريف معتمد بالمكان')

    def test_safe_approved_image_and_fallback_to_source_image(self):
        event = self.event()
        source_image = MediaAsset.objects.create(title_ar='source', external_url='https://example.com/source.jpg')
        EventMedia.objects.create(event=event, media_asset=source_image, role='cover')
        image = MediaAsset.objects.create(title_ar='editorial', external_url='https://example.com/editorial.jpg', alt_text_ar='وصف معتمد')
        self.feature(source_kind='event', event=event, image=image)
        self.assertEqual(self.hero().image_url, image.safe_url)
        self.assertEqual(self.hero().image_alt, 'وصف معتمد')
        for change in [dict(is_active=False), dict(is_active=True, media_type='document'),
                       dict(media_type='image', external_url='javascript:alert(1)')]:
            MediaAsset.objects.filter(pk=image.pk).update(**change)
            self.assertEqual(self.hero().image_url, source_image.safe_url)
        with TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            MediaAsset.objects.filter(pk=image.pk).update(external_url='', file='missing/editorial.jpg')
            self.assertEqual(self.hero().image_url, source_image.safe_url)
        image.delete()
        self.assertEqual(self.hero().image_url, source_image.safe_url)

    def test_summary_is_escaped_and_not_visible_for_invalid_source(self):
        event = self.event()
        self.feature(source_kind='event', event=event, summary_ar='<script>private()</script>')
        response = self.client.get('/')
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>private()</script>')
        event.is_public = False
        event.save()
        self.assertNotContains(self.client.get('/'), 'private()')

    def test_form_validation_source_shape_dates_and_image_type(self):
        event = self.event()
        program = Program.objects.create(code='program', name_ar='program')
        for feature in [HomepageFeature(source_kind='event'), HomepageFeature(source_kind='evergreen', event=event),
                        HomepageFeature(source_kind='event', event=event, program=program),
                        HomepageFeature(starts_at=NOW, ends_at=NOW)]:
            with self.assertRaises(ValidationError):
                feature.full_clean()
        document = MediaAsset.objects.create(title_ar='document', media_type='document', external_url='https://example.com/x.pdf')
        with self.assertRaises(ValidationError):
            HomepageFeature(image=document).full_clean()
        HomepageFeature(source_kind='event', event=event).full_clean()

    def test_database_single_slot_and_window_constraints(self):
        self.feature()
        with self.assertRaises(IntegrityError), transaction.atomic():
            HomepageFeature.objects.create(id=2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            HomepageFeature.objects.create()
        with self.assertRaises(IntegrityError), transaction.atomic():
            HomepageFeature.objects.filter(pk=1).update(starts_at=NOW, ends_at=NOW)

    def test_public_reads_do_not_write_and_revalidate_without_cache_eviction_jobs(self):
        self.feature(summary_ar='FIRST_COPY')
        with CaptureQueriesContext(connection) as queries:
            self.assertContains(self.client.get('/'), 'FIRST_COPY')
        self.assertFalse(any(q['sql'].lstrip().split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE'} for q in queries))
        HomepageFeature.objects.update(summary_ar='SECOND_COPY')
        response = self.client.get('/')
        self.assertContains(response, 'SECOND_COPY')
        self.assertNotContains(response, 'FIRST_COPY')
        self.assertIn('no-store', response['Cache-Control'])
        HomepageFeature.objects.all().delete()
        self.assertNotContains(self.client.get('/'), 'SECOND_COPY')

    @override_settings(PUBLIC_HOMEPAGE_ENABLED=False)
    def test_private_preview_works_before_release_without_publishing_or_writes(self):
        feature = self.feature(enabled=False, is_published=False, summary_ar='DRAFT_COPY', starts_at=NOW+timedelta(days=1))
        self.client.force_login(self.user())
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.preview_url())
        self.assertContains(response, 'DRAFT_COPY')
        self.assertContains(response, 'معاينة خاصة')
        self.assertContains(response, 'lang="ar"')
        self.assertContains(response, '<main ', count=1)
        self.assertContains(response, '<h1 ', count=1)
        self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('Cookie', response['Vary'])
        self.assertFalse(any(q['sql'].lstrip().split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE'} for q in queries))
        feature.refresh_from_db()
        self.assertFalse(feature.enabled or feature.is_published)
        self.assertTemplateUsed(self.client.get('/'), 'core/dashboard.html')

    def test_draft_preview_does_not_reveal_private_source_or_override(self):
        event = self.event('PRIVATE_TITLE', is_public=False, description_ar='PRIVATE_DESCRIPTION')
        image = MediaAsset.objects.create(title_ar='private', external_url='https://example.com/private-editorial.jpg')
        self.feature(source_kind='event', event=event, image=image, enabled=False, is_published=False, summary_ar='PRIVATE_SUMMARY')
        self.client.force_login(self.user())
        response = self.client.get(self.preview_url())
        for text in ['PRIVATE_TITLE', 'PRIVATE_DESCRIPTION', 'PRIVATE_SUMMARY', 'private-editorial.jpg']:
            self.assertNotContains(response, text)
        self.assertContains(response, 'غير مؤهل')

    def test_scheduled_preview_uses_damascus_time_and_live_gates(self):
        self.feature(summary_ar='SCHEDULED_COPY', starts_at=NOW+timedelta(hours=1), ends_at=NOW+timedelta(hours=2))
        self.client.force_login(self.user())
        before = self.client.get(self.preview_url(), {'mode': 'scheduled', 'at': '2026-10-31T15:00'})
        selected = self.client.get(self.preview_url(), {'mode': 'scheduled', 'at': '2026-10-31T16:00'})
        expired = self.client.get(self.preview_url(), {'mode': 'scheduled', 'at': '2026-10-31T17:00'})
        self.assertNotContains(before, 'SCHEDULED_COPY')
        self.assertContains(selected, 'SCHEDULED_COPY')
        self.assertNotContains(expired, 'SCHEDULED_COPY')
        for params in [{'at': 'invalid'}, {'at': '2026-13-55T99:99'}, {'mode': 'unknown'}]:
            self.assertEqual(self.client.get(self.preview_url(), params).status_code, 400)

    def test_preview_and_editor_are_superuser_only(self):
        for path in [self.preview_url(), reverse('admin:core_homepagefeature_changelist')]:
            self.assertEqual(self.client.get(path).status_code, 302)
        for values in [dict(is_staff=True, is_superuser=False), dict(is_staff=False, is_superuser=False),
                       dict(is_staff=True, is_superuser=True, is_active=False)]:
            user = self.user(**values)
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.preview_url()).status_code, 302)
            self.client.logout()
            user.delete()

    def test_admin_save_records_activity_and_django_audit_and_singleton_controls(self):
        user = self.user()
        self.client.force_login(user)
        response = self.client.post(reverse('admin:core_homepagefeature_add'), {
            'source_kind': 'evergreen', 'enabled': 'on', 'is_published': 'on',
            'summary_ar': 'ملخص معتمد', '_save': 'Save',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(HomepageFeature.objects.get().enabled)
        self.assertTrue(LogEntry.objects.filter(user=user, content_type__model='homepagefeature', action_flag=1).exists())
        self.assertTrue(ActivityLog.objects.filter(actor=user, action='homepage.feature.updated').exists())
        self.assertEqual(self.client.get(reverse('admin:core_homepagefeature_add')).status_code, 403)
        change = self.client.get(reverse('admin:core_homepagefeature_change', args=[1]))
        self.assertContains(change, self.preview_url())
        self.assertEqual(self.client.post(self.preview_url()).status_code, 405)
