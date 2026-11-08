"""Staff event cover/poster upload behavior and authorization."""
import base64
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalog.models import MediaAsset
from events.models import Event, EventMedia


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class EventArtworkUploadTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user(
            username='event-art-manager', phone='987711', password='test', role='admin',
        )
        self.event = Event.objects.create(
            title_ar='فعالية مصورة', starts_at=timezone.now(),
            status=Event.Status.PUBLISHED,
        )
        self.url = reverse('staff_event_artwork', args=[self.event.pk])
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = override_settings(MEDIA_ROOT=self.tmp.name)
        self.storage.enable()
        self.addCleanup(self.storage.disable)

    def upload(self, name='event.gif'):
        image = base64.b64decode('R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=')
        return SimpleUploadedFile(name, image, content_type='image/gif')

    def test_requires_staff_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_upload_creates_primary_event_cover(self):
        self.client.force_login(self.manager)
        result = self.client.post(self.url, {
            'role': 'cover', 'image': self.upload(), 'alt_text_ar': 'ملصق تجريبي',
        })
        self.assertEqual(result.status_code, 302)
        self.assertEqual(MediaAsset.objects.count(), 1)
        link = EventMedia.objects.get(event=self.event)
        self.assertEqual(link.role, EventMedia.Role.COVER)
        self.assertTrue(link.is_primary)
        self.assertTrue(link.media_asset.safe_url)
        self.assertEqual(link.media_asset.alt_text_ar, 'ملصق تجريبي')

    def test_second_upload_demotes_old_primary_without_deletion(self):
        self.client.force_login(self.manager)
        self.client.post(self.url, {'role': 'cover', 'image': self.upload()})
        self.client.post(self.url, {'role': 'poster', 'image': self.upload('event2.gif')})
        self.assertEqual(EventMedia.objects.filter(event=self.event).count(), 2)
        self.assertEqual(EventMedia.objects.filter(event=self.event, is_primary=True).count(), 1)

    def test_nonimage_extension_rejected(self):
        self.client.force_login(self.manager)
        response = self.client.post(self.url, {
            'role': 'cover',
            'image': SimpleUploadedFile('payload.txt', b'not an image', content_type='text/plain'),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'JPG')
        self.assertFalse(EventMedia.objects.exists())
        self.assertFalse(MediaAsset.objects.exists())
