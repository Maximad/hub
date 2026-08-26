import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib import admin
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import Client, SimpleTestCase, override_settings

from config import settings as hub_settings


class ProductionSettingsTests(SimpleTestCase):
    def test_production_security_settings(self):
        project_root = Path(__file__).resolve().parent.parent
        environment = os.environ.copy()
        environment.update(
            DJANGO_SETTINGS_MODULE='config.settings',
            DJANGO_DEBUG='False',
            DJANGO_SECRET_KEY='ci-valid-production-settings-test-secret',
            DJANGO_SECURE_SSL_REDIRECT='true',
            DJANGO_SESSION_COOKIE_SECURE='true',
            DJANGO_CSRF_COOKIE_SECURE='true',
        )
        code = (
            'import django; django.setup(); '
            'from django.conf import settings as s; '
            'assert s.DEBUG is False; '
            'assert s.SECURE_SSL_REDIRECT is True; '
            'assert s.SESSION_COOKIE_SECURE is True; '
            'assert s.CSRF_COOKIE_SECURE is True; '
            'assert s.CSRF_COOKIE_HTTPONLY is False; '
            'assert s.SECURE_HSTS_SECONDS == 3600; '
            'assert s.SECURE_HSTS_INCLUDE_SUBDOMAINS is False; '
            'assert s.SECURE_HSTS_PRELOAD is False; '
            'assert s.SECURE_CONTENT_TYPE_NOSNIFF is True; '
            'assert s.SECURE_REFERRER_POLICY == "strict-origin-when-cross-origin"; '
            'assert s.X_FRAME_OPTIONS == "DENY"'
        )
        result = subprocess.run(
            [sys.executable, '-c', code], cwd=project_root,
            env=environment, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_boolean_parser_is_strict(self):
        with mock.patch.dict(os.environ, {'TEST_BOOLEAN': 'perhaps'}):
            with self.assertRaises(ValueError):
                hub_settings.optional_bool_env('TEST_BOOLEAN')

    def test_production_refuses_missing_or_placeholder_secrets(self):
        project_root = Path(__file__).resolve().parent.parent
        for secret in ('', 'change-this-in-production', 'django-insecure-example'):
            environment = os.environ.copy()
            environment.update(DJANGO_DEBUG='False', DJANGO_SECRET_KEY=secret)
            result = subprocess.run(
                [sys.executable, '-c', 'import config.settings'], cwd=project_root,
                env=environment, text=True, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('DJANGO_SECRET_KEY must be set', result.stderr)


@override_settings(
    DEBUG=False, ALLOWED_HOSTS=['hubsweida.jwtalenthouse.com'],
    SECURE_SSL_REDIRECT=True, SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True, SECURE_HSTS_SECONDS=3600,
    SESSION_ENGINE='django.contrib.sessions.backends.signed_cookies',
)
class ProxySecurityTests(SimpleTestCase):
    def setUp(self):
        self.https_client = Client(
            HTTP_HOST='hubsweida.jwtalenthouse.com',
            HTTP_X_FORWARDED_PROTO='https',
        )

    def _menu_patches(self):
        return (
            mock.patch('core.views.menu._menu_context', return_value={}),
            mock.patch('core.views.menu.decorate_menu_context', side_effect=lambda context, table=None: context),
        )

    def test_menu_and_admin_login_work_behind_https_proxy(self):
        menu_context, decorate = self._menu_patches()
        with menu_context, decorate:
            self.assertEqual(self.https_client.get('/menu/').status_code, 200)
        with mock.patch.object(admin.site, 'each_context', return_value={}):
            self.assertEqual(self.https_client.get('/admin/login/').status_code, 200)

    @override_settings(STORAGES={'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    }})
    def test_rendered_menu_uses_pinned_local_htmx_asset(self):
        menu_context, decorate = self._menu_patches()
        with menu_context, decorate:
            response = self.https_client.get('/menu/')

        self.assertContains(response, '<script src="/static/js/htmx.min.js"></script>', html=True)
        rendered = response.content.decode()
        self.assertNotRegex(rendered, r'https?://[^\"\s]*htmx')

    def test_staff_redirect_works_behind_https_proxy(self):
        response = self.https_client.get('/staff/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/admin/login/?next=/staff/')

    def test_plain_http_redirects_to_https(self):
        response = Client(HTTP_HOST='hubsweida.jwtalenthouse.com').get('/menu/')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.url, 'https://hubsweida.jwtalenthouse.com/menu/')

    def test_security_headers_are_sent_over_proxy_https(self):
        with mock.patch.object(admin.site, 'each_context', return_value={}):
            response = self.https_client.get('/admin/login/')
        self.assertEqual(response.headers['Strict-Transport-Security'], 'max-age=3600')
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response.headers['Referrer-Policy'], 'strict-origin-when-cross-origin')
        self.assertEqual(response.headers['X-Frame-Options'], 'DENY')

    def test_session_and_csrf_cookies_are_secure(self):
        request = Client(HTTP_HOST='hubsweida.jwtalenthouse.com').request().wsgi_request

        def save_session(request):
            request.session['test'] = True
            return HttpResponse('ok')

        response = SessionMiddleware(save_session)(request)
        self.assertTrue(response.cookies[settings.SESSION_COOKIE_NAME]['secure'])

        with mock.patch.object(admin.site, 'each_context', return_value={}):
            response = self.https_client.get('/admin/login/')
        self.assertTrue(response.cookies[settings.CSRF_COOKIE_NAME]['secure'])

    def test_csrf_rejections_have_a_dedicated_console_logger(self):
        logger = settings.LOGGING['loggers']['django.security.csrf']
        self.assertEqual(logger['handlers'], ['console'])
        self.assertEqual(logger['level'], 'WARNING')
        self.assertFalse(logger['propagate'])
