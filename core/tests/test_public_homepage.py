from dataclasses import fields
from datetime import datetime, timedelta, timezone as dt_timezone
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import resolve, reverse

from catalog.models import MediaAsset
from core.models import Room, SystemSetting, TableArea
from core.services.public_homepage import PublicEvent, public_homepage_context
from core.settings_helpers import get_system_settings
from events.models import Event, EventMedia


NOW = datetime(2026, 10, 31, 12, 0, tzinfo=dt_timezone.utc)


@override_settings(PUBLIC_HOMEPAGE_ENABLED=True, ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class PublicHomepageTests(TestCase):
    def setUp(self):
        get_system_settings.cache_clear()
        self.clock = patch('django.utils.timezone.now', return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.addCleanup(get_system_settings.cache_clear)

    def event(self, **kwargs):
        defaults = dict(title_ar='لقاء ثقافي', starts_at=NOW + timedelta(days=1),
                        status=Event.Status.PUBLISHED, is_public=True)
        defaults.update(kwargs)
        return Event.objects.create(**defaults)

    def detail_url(self, event):
        return reverse('public_event_detail', kwargs={'uuid': event.uuid})

    def test_anonymous_homepage_identity_navigation_and_semantics(self):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        for text in ['هَبّ السويداء', 'مشاريب', 'سُفرة', 'أوضة', 'الجنينة',
                     'ما في فعاليات معلنة', 'dir="rtl"', 'lang="ar"',
                     'href="/menu/"', 'href="/me/"', 'href="/events/"']:
            self.assertContains(response, text)
        self.assertContains(response, '<main ', count=1)
        self.assertContains(response, '<h1 ', count=1)
        self.assertNotContains(response, '/staff/')
        self.assertNotContains(response, 'احجز الآن')
        self.assertNotContains(response, '/programs/')
        self.assertNotContains(response, '/popups/')
        self.assertContains(response, 'public_homepage.css')
        self.assertIn('no-store', response['Cache-Control'])

    def test_public_arabic_language_independent_of_staff_language_preference(self):
        SystemSetting.objects.create(default_language='en')
        get_system_settings.cache_clear()
        self.assertContains(self.client.get('/'), 'lang="ar"')

    def test_current_eligibility_boundaries_and_stable_chronology(self):
        ongoing = self.event(title_ar='جارية', starts_at=NOW-timedelta(hours=1), ends_at=NOW+timedelta(hours=1))
        at_start = self.event(title_ar='تبدأ الآن', starts_at=NOW)
        future1 = self.event(title_ar='لاحقة أ', starts_at=NOW+timedelta(hours=1))
        future2 = self.event(title_ar='لاحقة ب', starts_at=future1.starts_at)
        for status in [Event.Status.DRAFT, Event.Status.CANCELLED, Event.Status.COMPLETED]:
            self.event(title_ar='غير منشورة '+status, status=status)
        self.event(title_ar='خاص', is_public=False)
        self.event(title_ar='بدون نهاية ومضت', starts_at=NOW-timedelta(seconds=1))
        self.event(title_ar='انتهت الآن', starts_at=NOW-timedelta(hours=1), ends_at=NOW)
        context = public_homepage_context(now=NOW)
        self.assertEqual([e.title for e in context['events']], ['جارية', 'تبدأ الآن', 'لاحقة أ'])
        self.assertEqual(context['featured_event'].url, self.detail_url(ongoing))
        self.assertTrue(context['events'][0].is_ongoing)
        self.assertFalse(context['events'][1].is_ongoing)
        self.assertFalse(context['events'][1].is_past)
        listing = self.client.get(reverse('public_event_list'))
        self.assertEqual([e.url for e in listing.context['events']],
                         [self.detail_url(e) for e in (ongoing, at_start, future1, future2)])
        for title in ['غير منشورة', 'خاص', 'بدون نهاية ومضت', 'انتهت الآن']:
            self.assertNotContains(listing, title)

    def test_damascus_time_even_with_different_active_timezone(self):
        event = self.event(starts_at=NOW, ends_at=NOW+timedelta(hours=2))
        from django.utils import timezone
        with timezone.override('America/New_York'):
            response = self.client.get(self.detail_url(event))
        self.assertContains(response, '15:00')
        self.assertContains(response, '17:00')
        self.assertContains(response, '31 / 10 / 2026')
        self.assertContains(response, '+03:00')
        self.assertContains(response, 'بتوقيت دمشق')

    def test_detail_publication_gate_and_unknown_uuid(self):
        for status in [Event.Status.DRAFT, Event.Status.CANCELLED, Event.Status.COMPLETED]:
            event = self.event(status=status, description_ar='private marker')
            self.assertEqual(self.client.get(self.detail_url(event)).status_code, 404)
        private = self.event(is_public=False)
        self.assertEqual(self.client.get(self.detail_url(private)).status_code, 404)
        self.assertEqual(self.client.get(reverse('public_event_detail', kwargs={'uuid': uuid4()})).status_code, 404)

    def test_publication_is_rechecked_without_stale_promotions(self):
        event = self.event()
        self.assertContains(self.client.get('/'), event.title_ar)
        for change in [dict(status=Event.Status.CANCELLED), dict(status=Event.Status.DRAFT),
                       dict(status=Event.Status.PUBLISHED, is_public=False)]:
            Event.objects.filter(pk=event.pk).update(**change)
            self.assertNotContains(self.client.get('/'), event.title_ar)
            self.assertEqual(self.client.get(self.detail_url(event)).status_code, 404)
        Event.objects.filter(pk=event.pk).delete()
        self.assertEqual(self.client.get(self.detail_url(event)).status_code, 404)

    def test_feature_falls_forward_when_first_event_expires(self):
        first = self.event(starts_at=NOW, title_ar='الأولى')
        second = self.event(starts_at=NOW+timedelta(hours=1), title_ar='الثانية')
        self.assertEqual(public_homepage_context(now=NOW)['featured_event'].url, self.detail_url(first))
        self.assertEqual(public_homepage_context(now=NOW+timedelta(seconds=1))['featured_event'].url, self.detail_url(second))
        self.assertIsNone(public_homepage_context(now=NOW+timedelta(hours=2))['featured_event'])

    def test_past_published_detail_is_labelled_not_promoted(self):
        event = self.event(starts_at=NOW-timedelta(days=1))
        self.assertContains(self.client.get(self.detail_url(event)), 'هذا الموعد مضى')
        self.assertEqual(public_homepage_context(now=NOW)['events'], ())

    def test_safe_view_model_and_escaped_text(self):
        room = Room.objects.create(name_ar='INTERNAL ROOM')
        event = self.event(title_ar='<script>alert(1)</script>', description_ar='<img src=x onerror=alert(1)>',
                           capacity=7421, room=room)
        response = self.client.get(self.detail_url(event))
        self.assertContains(response, '&lt;script&gt;')
        self.assertContains(response, '&lt;img')
        self.assertNotContains(response, '<script>alert(1)</script>')
        for private in ['INTERNAL ROOM', '7421', 'private marker']:
            self.assertNotContains(response, private)
        self.assertEqual({f.name for f in fields(PublicEvent)}, {
            'title', 'description', 'url', 'starts_at', 'ends_at', 'image_url', 'image_alt', 'is_ongoing', 'is_past',
        })

    def test_only_active_public_artwork_and_safe_url(self):
        event = self.event()
        inactive = MediaAsset.objects.create(title_ar='PRIVATE ASSET', external_url='https://example.com/private.jpg', is_active=False)
        EventMedia.objects.create(event=event, media_asset=inactive, role=EventMedia.Role.COVER, is_primary=True)
        unsafe = MediaAsset.objects.create(title_ar='unsafe', external_url='javascript:alert(1)')
        EventMedia.objects.create(event=event, media_asset=unsafe, role=EventMedia.Role.COVER, is_primary=True)
        gallery = MediaAsset.objects.create(title_ar='gallery', external_url='https://example.com/gallery.jpg')
        EventMedia.objects.create(event=event, media_asset=gallery, role=EventMedia.Role.GALLERY)
        image = MediaAsset.objects.create(title_ar='public', external_url='https://example.com/public.jpg', alt_text_ar='ملصق اللقاء')
        link = EventMedia.objects.create(event=event, media_asset=image, role=EventMedia.Role.POSTER)
        response = self.client.get(self.detail_url(event))
        self.assertContains(response, image.safe_url)
        self.assertContains(response, 'ملصق اللقاء')
        for secret in ['PRIVATE ASSET', 'private.jpg', 'gallery.jpg', 'javascript:']:
            self.assertNotContains(response, secret)
        link.is_active = False
        link.save(update_fields=['is_active'])
        self.assertEqual(public_homepage_context(now=NOW)['featured_event'].image_url, '')

    def test_missing_or_deleted_media_has_text_fallback(self):
        event = self.event()
        with TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            image = MediaAsset.objects.create(title_ar='missing', file='events/missing.jpg')
            EventMedia.objects.create(event=event, media_asset=image, role=EventMedia.Role.COVER)
            response = self.client.get(self.detail_url(event))
            self.assertContains(response, 'مساحة')
            self.assertNotContains(response, 'missing.jpg')
            image.delete()
            self.assertEqual(self.client.get(self.detail_url(event)).status_code, 200)

    def test_public_get_does_not_generate_thumbnail_files(self):
        event = self.event()
        image = MediaAsset.objects.create(title_ar='poster', external_url='https://example.com/poster.jpg')
        EventMedia.objects.create(event=event, media_asset=image, role=EventMedia.Role.POSTER)
        with patch.object(MediaAsset, 'generate_thumbnail', side_effect=AssertionError('GET must not write')):
            self.assertEqual(self.client.get(self.detail_url(event)).status_code, 200)

    def test_bounded_queries_homepage_and_pagination(self):
        for index in range(14):
            event = self.event(title_ar=f'لقاء {index}')
            asset = MediaAsset.objects.create(title_ar='poster', external_url='https://example.com/poster.jpg')
            EventMedia.objects.create(event=event, media_asset=asset, role=EventMedia.Role.POSTER)
        with self.assertNumQueries(2):
            context = public_homepage_context(now=NOW)
        self.assertEqual(len(context['events']), 3)
        first = self.client.get('/events/')
        self.assertEqual(len(first.context['events']), 12)
        second = self.client.get('/events/?page=2')
        self.assertEqual(len(second.context['events']), 2)
        self.assertNotContains(second, 'href="?page=3"')

    def test_only_safe_methods_no_public_mutations(self):
        event = self.event()
        for path in ['/', '/events/', self.detail_url(event)]:
            self.assertEqual(self.client.head(path).status_code, 200)
            for method in ['post', 'put', 'patch', 'delete']:
                self.assertEqual(getattr(self.client, method)(path).status_code, 405)
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.PUBLISHED)
        self.assertEqual(Event.objects.count(), 1)

    @override_settings(PUBLIC_HOMEPAGE_ENABLED=False)
    def test_disabled_release_preserves_legacy_root_and_hides_new_routes(self):
        event = self.event()
        self.assertTemplateUsed(self.client.get('/'), 'core/dashboard.html')
        self.assertEqual(self.client.get('/events/').status_code, 404)
        self.assertEqual(self.client.get(self.detail_url(event)).status_code, 404)
        self.assertEqual(reverse('dashboard'), '/')

    def test_existing_customer_menu_qr_staff_and_provider_entrypoints(self):
        room = Room.objects.create(name_ar='مشاريب')
        table = TableArea.objects.create(room=room, name_ar='طاولة')
        qr = f'/menu/table/{table.qr_token}/'
        self.assertEqual(self.client.get('/menu/').status_code, 200)
        self.assertEqual(resolve(qr).url_name, 'menu_table')
        self.assertIn(self.client.get(qr).status_code, [200, 302])
        self.assertEqual(self.client.get('/me/').status_code, 302)
        self.assertEqual(self.client.get('/staff/').status_code, 302)
        self.assertEqual(self.client.get('/provider/').status_code, 302)
        menu = self.client.get('/menu/')
        self.assertNotContains(menu, 'public_homepage.css')
        self.assertContains(menu, 'aria-label="العودة إلى المنيو"')

    def test_authenticated_staff_still_gets_public_shell_at_root(self):
        user = get_user_model().objects.create_user(username='homepage-staff', password='test', role='admin', is_staff=True)
        self.client.force_login(user)
        response = self.client.get('/')
        self.assertContains(response, 'ph-header')
        self.assertNotContains(response, '/staff/')
