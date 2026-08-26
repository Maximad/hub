import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from core.models import Category, Order, Product


PRODUCTION_HOST = 'hubsweida.jwtalenthouse.com'
PROXY = {'HTTP_HOST': PRODUCTION_HOST, 'HTTP_X_FORWARDED_PROTO': 'https'}
EXTERNAL_POST_EXEMPTIONS = {
    'menu/hotspot_connect.html': 'data-csrf-audit="external-post"',
}


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
        get_user_model().objects.create_superuser(
            username='rotation-admin', password='pass', email='rotation@example.com',
            phone='+963922222222')
        login_page = client.get(reverse('admin:login'))
        self.assertEqual(login_page.status_code, 200)
        login_token = re.search(
            r'name="csrfmiddlewaretoken" value="([^"]+)"',
            login_page.content.decode(),
        ).group(1)
        login_response = client.post(reverse('admin:login'), {
            'csrfmiddlewaretoken': login_token,
            'username': 'rotation-admin',
            'password': 'pass',
            'next': reverse('admin:index'),
        }, HTTP_REFERER=f'https://{PRODUCTION_HOST}{reverse("admin:login")}')
        self.assertEqual(login_response.status_code, 302)
        self.assertEqual(login_response.url, reverse('admin:index'))
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
            if not re.search(r'<form\b[^>]*\bmethod\s*=\s*["\']?post', source, re.I):
                continue
            relative_path = str(template.relative_to(root))
            if '{% csrf_token %}' in source:
                continue
            marker = EXTERNAL_POST_EXEMPTIONS.get(relative_path)
            if marker and marker in source:
                continue
            offenders.append(relative_path)
        self.assertEqual(offenders, [])

    def test_external_post_exemption_is_narrow_and_explicit(self):
        self.assertEqual(
            EXTERNAL_POST_EXEMPTIONS,
            {'menu/hotspot_connect.html': 'data-csrf-audit="external-post"'},
        )
        root = Path(__file__).resolve().parents[2] / 'templates'
        source = (root / 'menu/hotspot_connect.html').read_text()
        self.assertIn('data-csrf-audit="external-post"', source)
        self.assertIn('action="{{ login_url }}"', source)
        self.assertNotIn('{% csrf_token %}', source)
