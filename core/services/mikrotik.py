"""Minimal, deliberately narrow RouterOS v7 REST client."""
import json
import ssl
from base64 import b64encode
from urllib import error, parse, request


class MikroTikError(Exception):
    """A safe-to-display RouterOS integration error."""


class MikroTikConfigurationError(MikroTikError): pass
class MikroTikConnectionError(MikroTikError): pass
class MikroTikAuthenticationError(MikroTikError): pass
class MikroTikProvisioningError(MikroTikError): pass


class RouterOSClient:
    """Only the RouterOS resources owned/needed by Hub are exposed here."""

    def __init__(self, *, base_url, username, password, verify_tls=True, ca_file='',
                 connect_timeout=5, read_timeout=10):
        if not base_url or not username or not password:
            raise MikroTikConfigurationError('إعدادات اتصال MikroTik غير مكتملة.')
        if not base_url.startswith('https://'):
            raise MikroTikConfigurationError('يجب أن يستخدم عنوان MikroTik بروتوكول HTTPS.')
        self.base_url = base_url.rstrip('/')
        if not self.base_url.endswith('/rest'):
            self.base_url += '/rest'
        self.timeout = float(connect_timeout) + float(read_timeout)
        self.auth = 'Basic ' + b64encode(f'{username}:{password}'.encode()).decode()
        try:
            self.context = (ssl.create_default_context(cafile=ca_file or None) if verify_tls
                            else ssl._create_unverified_context())
        except (OSError, ssl.SSLError, ValueError) as exc:
            raise MikroTikConfigurationError('تعذر تحميل إعداد شهادة MikroTik.') from exc

    def _call(self, method, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = request.Request(self.base_url + '/' + path.lstrip('/'), data=data, method=method,
                              headers={'Authorization': self.auth, 'Content-Type': 'application/json'})
        try:
            with request.urlopen(req, timeout=self.timeout, context=self.context) as response:
                body = response.read()
                return json.loads(body) if body else {}
        except error.HTTPError as exc:
            if exc.code in (401, 403):
                raise MikroTikAuthenticationError('رفض MikroTik بيانات اعتماد حساب الخدمة.') from exc
            raise MikroTikProvisioningError(f'رفض MikroTik العملية (HTTP {exc.code}).') from exc
        except (error.URLError, TimeoutError, ssl.SSLError, OSError) as exc:
            raise MikroTikConnectionError('تعذر الاتصال بـ MikroTik أو التحقق من شهادة TLS.') from exc
        except (ValueError, json.JSONDecodeError) as exc:
            raise MikroTikConnectionError('أعاد MikroTik استجابة غير صالحة.') from exc

    def system_resource(self): return self._call('GET', 'system/resource')

    def find_hotspot_user(self, name):
        rows = self._call('GET', 'ip/hotspot/user?' + parse.urlencode({'.proplist': '.id,name,comment,disabled', 'name': name}))
        return rows[0] if rows else None

    def create_hotspot_user(self, values): return self._call('PUT', 'ip/hotspot/user', values)
    def update_hotspot_user(self, remote_id, values): return self._call('PATCH', f'ip/hotspot/user/{parse.quote(remote_id)}', values)
    def find_profile(self, name):
        rows = self._call('GET', 'ip/hotspot/user/profile?' + parse.urlencode({'.proplist': '.id,name', 'name': name}))
        return rows[0] if rows else None
    def active_sessions(self, name):
        return self._call('GET', 'ip/hotspot/active?' + parse.urlencode({'.proplist': '.id,user', 'user': name}))
    def remove_active(self, remote_id): return self._call('DELETE', f'ip/hotspot/active/{parse.quote(remote_id)}')
