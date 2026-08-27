"""Network enforcement for package-less InternetSession access."""
import re
import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import InternetBandwidthProfile, InternetSession
from core.services.mikrotik import (
    MikroTikConfigurationError,
    MikroTikError,
    MikroTikProvisioningError,
    RouterOSClient,
)
from internet.models import InternetSessionNetworkState


NOT_PROVISIONED = 'not_provisioned'
PROVISIONED = 'provisioned'
DISCONNECTED = 'disconnected'
PROVISION_ERROR = 'provision_error'


def _safe_network_error(exc):
    text = str(exc).replace('\r', ' ').replace('\n', ' ')
    lowered = text.lower()
    if any(marker in lowered for marker in (
        'password', 'authorization', 'credential', 'mikrotik_password',
    )):
        return 'Network operation failed; sensitive details were removed.'
    return text[:500]


def _state(session):
    return InternetSessionNetworkState.objects.get_or_create(session=session)[0]


def _save_state(session, *, status, error=''):
    now = timezone.now()
    session.network_status = status
    session.save(update_fields=['network_status', 'updated_at'])
    state = _state(session)
    state.last_network_sync_at = now
    state.last_network_error = error
    state.save(update_fields=['last_network_sync_at', 'last_network_error', 'updated_at'])
    return state


class ManualSessionNetworkBackend:
    code = 'manual'

    def provision_access(self, session):
        _save_state(session, status=PROVISIONED)
        return session

    def disconnect_access(self, session):
        _save_state(session, status=DISCONNECTED)
        return session

    def refresh_access(self, session):
        return self.provision_access(session) if session.status == InternetSession.Status.ACTIVE else self.disconnect_access(session)

    def test_connection(self):
        return True


class MikroTikSessionNetworkBackend:
    code = 'mikrotik'

    def __init__(self, client=None):
        if not settings.MIKROTIK_ENABLED:
            raise MikroTikConfigurationError('تكامل MikroTik معطّل.')
        self.client = client or RouterOSClient(
            base_url=settings.MIKROTIK_BASE_URL,
            username=settings.MIKROTIK_USERNAME,
            password=settings.MIKROTIK_PASSWORD,
            verify_tls=settings.MIKROTIK_VERIFY_TLS,
            ca_file=settings.MIKROTIK_CA_FILE,
            connect_timeout=settings.MIKROTIK_CONNECT_TIMEOUT,
            read_timeout=settings.MIKROTIK_READ_TIMEOUT,
        )
        if not settings.MIKROTIK_HOTSPOT_SERVER:
            raise MikroTikConfigurationError('اسم خادم HotSpot الخاص بـ Hub غير مضبوط.')

    @staticmethod
    def username(session):
        prefix = re.sub(r'[^A-Za-z0-9_.-]', '-', settings.MIKROTIK_USER_PREFIX)[:22]
        return f'{prefix}s-{session.public_code.hex}'[:63]

    @staticmethod
    def ownership(session):
        return f'hub-session:{session.pk}'

    def _fernet(self):
        try:
            from cryptography.fernet import Fernet
        except ImportError as exc:
            raise MikroTikConfigurationError('مكتبة تشفير بيانات دخول الشبكة غير مثبتة.') from exc
        if not settings.MIKROTIK_CREDENTIAL_KEY:
            raise MikroTikConfigurationError('مفتاح تشفير بيانات دخول الشبكة غير مضبوط.')
        try:
            return Fernet(settings.MIKROTIK_CREDENTIAL_KEY.encode())
        except (ValueError, TypeError) as exc:
            raise MikroTikConfigurationError('مفتاح تشفير بيانات دخول الشبكة غير صالح.') from exc

    def _credential(self, session):
        from cryptography.fernet import InvalidToken
        state = _state(session)
        cipher = self._fernet()
        if state.network_credential_encrypted:
            try:
                return cipher.decrypt(state.network_credential_encrypted.encode()).decode(), None
            except InvalidToken as exc:
                raise MikroTikConfigurationError('تعذر فك بيانات دخول الشبكة المخزنة.') from exc
        password = secrets.token_urlsafe(24)
        return password, cipher.encrypt(password.encode()).decode()

    def _profile(self, session):
        profile = settings.MIKROTIK_DEFAULT_PROFILE
        if session.bandwidth_profile:
            mapping = InternetBandwidthProfile.objects.filter(code=session.bandwidth_profile).first()
            if not mapping or not mapping.router_profile_name:
                raise MikroTikConfigurationError(
                    'ملف السرعة المحدد للجلسة غير مربوط بملف RouterOS.'
                )
            profile = mapping.router_profile_name
        if not profile:
            raise MikroTikConfigurationError('لا يوجد ملف RouterOS مطابق لملف السرعة.')
        return profile

    def plan(self, session):
        values = {
            'name': self.username(session),
            'server': settings.MIKROTIK_HOTSPOT_SERVER,
            'profile': self._profile(session),
            'comment': self.ownership(session),
            'disabled': 'false',
        }
        if session.device_mac:
            values['mac-address'] = session.device_mac
        return values

    def _owned_user(self, session):
        user = self.client.find_hotspot_user(self.username(session))
        if user and user.get('comment') != self.ownership(session):
            raise MikroTikProvisioningError('تعارض اسم مستخدم RouterOS مع مورد لا يملكه Hub.')
        return user

    def _record_failure(self, session, exc):
        _save_state(session, status=PROVISION_ERROR, error=_safe_network_error(exc))

    def provision_access(self, session):
        encrypted = None
        try:
            values = self.plan(session)
            user = self._owned_user(session)
            if not self.client.find_profile(values['profile']):
                raise MikroTikConfigurationError('ملف مستخدم RouterOS المحدد غير موجود.')
            state = _state(session)
            if user:
                if not state.network_credential_encrypted:
                    password, encrypted = self._credential(session)
                    values = {**values, 'password': password}
                self.client.update_hotspot_user(user['.id'], values)
            else:
                password, encrypted = self._credential(session)
                self.client.create_hotspot_user({**values, 'password': password})
        except (MikroTikError, ValidationError) as exc:
            self._record_failure(session, exc)
            raise

        session.network_session_id = values['name']
        session.network_status = PROVISIONED
        session.save(update_fields=['network_session_id', 'network_status', 'updated_at'])
        state = _state(session)
        if encrypted:
            state.network_credential_encrypted = encrypted
        state.last_network_error = ''
        state.last_network_sync_at = timezone.now()
        state.save(update_fields=[
            'network_credential_encrypted', 'last_network_error',
            'last_network_sync_at', 'updated_at',
        ])
        return session

    def connection_credentials(self, session):
        if session.network_status != PROVISIONED:
            raise ValidationError('لم يكتمل تجهيز بيانات دخول الشبكة.')
        state = _state(session)
        if not state.network_credential_encrypted:
            raise MikroTikConfigurationError('بيانات دخول الشبكة المشفرة غير موجودة.')
        password, encrypted = self._credential(session)
        if encrypted is not None:
            raise MikroTikConfigurationError('تعذر قراءة بيانات دخول الشبكة بأمان.')
        return session.network_session_id or self.username(session), password

    def refresh_access(self, session):
        if session.status != InternetSession.Status.ACTIVE:
            return self.disconnect_access(session)
        return self.provision_access(session)

    def disconnect_access(self, session):
        try:
            user = self._owned_user(session)
            if user:
                self.client.update_hotspot_user(user['.id'], {'disabled': 'true'})
            username = self.username(session)
            for active in self.client.active_sessions(username):
                if active.get('user') == username:
                    self.client.remove_active(active['.id'])
        except MikroTikError as exc:
            self._record_failure(session, exc)
            raise
        _save_state(session, status=DISCONNECTED)
        return session

    def test_connection(self):
        resource = self.client.system_resource()
        if not isinstance(resource, dict):
            raise MikroTikConfigurationError('استجابة فحص MikroTik غير صالحة.')
        return True


def get_session_network_backend(code='manual'):
    if code == InternetSession.NetworkProvider.MANUAL:
        return ManualSessionNetworkBackend()
    if code == InternetSession.NetworkProvider.MIKROTIK:
        return MikroTikSessionNetworkBackend()
    raise ValidationError('مزود تنفيذ الشبكة للجلسة غير مدعوم.')
