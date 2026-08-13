import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from core.models import Category, Order, Product


PRODUCTION_HOST = 'hubsweida.jwtalenthouse.com'
PROXY = {'HTTP_HOST': PRODUCTION_HOST, 'HTTP_X_FORWARDED_PROTO': 'https'}


@override_settings(
    DEBUG=False,
    ALLOWED_HOSTS=[PRODUCTION_HOST],
    CSRF_TRUSTED_ORIGINS=[f'https://{PRODUCTION_HOST}'],
    SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
    SECURE_SSL_REDIRECT=True,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class BrowserCsrfHardeningTests(TestCase):
    def setUp(self):
        category = Category.objects.create(name_ar='مشروبات')
        self.product = Product.objects.create(
            category=category, name_ar='قهوة', price_syp=1000,
            is_available=True, visible_on_qr=True, orderable_on_qr=True,
        )

    def _client(self):
        return Client(enforce_csrf_checks=True, **PROXY)

    def _menu_token(self, client):
        response = client.get(reverse('menu_public'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('csrftoken', response.cookies)
        match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', response.content.decode())
        self.assertIsNotNone(match)
        return match.group(1)

    def _payload(self, token):
        return {
            'csrfmiddlewaretoken': token,
            f'qty_{self.product.pk}': '1',
            'fulfillment_mode': Order.FulfillmentMode.INSIDE_SPACE,
        }

    def test_anonymous_menu_post_accepts_origin_and_rejects_cross_origin(self):
        client = self._client()
        token = self._menu_token(client)
        accepted = client.post(
            reverse('menu_public'), self._payload(token),
            HTTP_ORIGIN=f'https://{PRODUCTION_HOST}',
        )
        self.assertEqual(accepted.status_code, 302)

        other = self._client()
        token = self._menu_token(other)
        rejected = other.post(
            reverse('menu_public'), self._payload(token),
            HTTP_ORIGIN='https://attacker.example',
        )
        self.assertEqual(rejected.status_code, 403)

    def test_authenticated_cashier_menu_post_accepts_referer_fallback(self):
        user = get_user_model().objects.create_user(
            username='csrf-cashier', password='pass', phone='+963911111111', role='cashier')
        client = self._client()
        client.force_login(user)
        token = self._menu_token(client)
        response = client.post(
            reverse('menu_public'), self._payload(token),
            HTTP_REFERER=f'https://{PRODUCTION_HOST}/menu/',
        )
        self.assertEqual(response.status_code, 302)

    def test_independent_browsers_each_use_their_own_csrf_secret(self):
        for client in (self._client(), self._client()):
            token = self._menu_token(client)
            response = client.post(
                reverse('menu_public'), self._payload(token),
                HTTP_ORIGIN=f'https://{PRODUCTION_HOST}',
            )
            self.assertEqual(response.status_code, 302)

    def test_login_rotation_rejects_stale_markup_and_helper_refreshes_it(self):
        client = self._client()
        stale_token = self._menu_token(client)
        old_cookie = client.cookies['csrftoken'].value
        user = get_user_model().objects.create_user(
            username='rotation-cashier', password='pass', phone='+963922222222', role='cashier')
        client.force_login(user)
        self.assertNotEqual(client.cookies['csrftoken'].value, old_cookie)
        response = client.post(
            reverse('menu_public'), self._payload(stale_token),
            HTTP_ORIGIN=f'https://{PRODUCTION_HOST}',
        )
        self.assertEqual(response.status_code, 403)

        source = (Path(__file__).resolve().parents[2] / 'static/js/csrf.js').read_text()
        self.assertIn("addEventListener('submit'", source)
        self.assertIn("input.value = current", source)

        # Browser submit synchronization replaces the stale hidden value with
        # the newly rotated same-origin cookie before the request is sent.
        fresh_token = client.cookies['csrftoken'].value
        response = client.post(
            reverse('menu_public'), self._payload(fresh_token),
            HTTP_ORIGIN=f'https://{PRODUCTION_HOST}',
        )
        self.assertEqual(response.status_code, 302)


class CsrfTemplateAuditTests(SimpleTestCase):
    def test_post_forms_in_templates_have_a_csrf_token(self):
        root = Path(__file__).resolve().parents[2] / 'templates'
        offenders = []
        for template in root.rglob('*.html'):
            source = template.read_text()
            if re.search(r'<form\b[^>]*\bmethod\s*=\s*["\']?post', source, re.I):
                if '{% csrf_token %}' not in source:
                    offenders.append(str(template.relative_to(root)))
        self.assertEqual(offenders, [])
