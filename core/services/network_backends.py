"""Network enforcement adapters; Hub remains the commercial authority."""
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import InternetBandwidthProfile
from core.services.mikrotik import (MikroTikConfigurationError, MikroTikConnectionError, MikroTikError,
                                     MikroTikProvisioningError, RouterOSClient)


class ManualNetworkBackend:
    code = 'manual'
    def provision_access(self, entitlement):
        if entitlement.effective_status() in {entitlement.Status.EXPIRED, entitlement.Status.CANCELLED}:
            raise ValidationError('لا يمكن تجهيز استحقاق منتهٍ أو ملغى.')
        entitlement.network_status = entitlement.NetworkStatus.PROVISIONED
        entitlement.last_network_error = ''
        entitlement.last_network_sync_at = timezone.now()
        entitlement.save(update_fields=['network_status', 'last_network_error', 'last_network_sync_at', 'updated_at'])
        return entitlement
    def disconnect_access(self, entitlement):
        entitlement.network_status = entitlement.NetworkStatus.DISCONNECTED
        entitlement.last_network_sync_at = timezone.now()
        entitlement.save(update_fields=['network_status', 'last_network_sync_at', 'updated_at'])
        return entitlement
    def refresh_access(self, entitlement):
        entitlement.last_network_sync_at = timezone.now()
        entitlement.save(update_fields=['last_network_sync_at', 'updated_at'])
        return entitlement
    def expire_access(self, entitlement): return self.disconnect_access(entitlement)
    def test_connection(self): return True


class MikroTikNetworkBackend:
    code = 'mikrotik'

    def __init__(self, client=None):
        if not settings.MIKROTIK_ENABLED:
            raise MikroTikConfigurationError('تكامل MikroTik معطّل.')
        self.client = client or RouterOSClient(
            base_url=settings.MIKROTIK_BASE_URL, username=settings.MIKROTIK_USERNAME,
            password=settings.MIKROTIK_PASSWORD, verify_tls=settings.MIKROTIK_VERIFY_TLS,
            ca_file=settings.MIKROTIK_CA_FILE, connect_timeout=settings.MIKROTIK_CONNECT_TIMEOUT,
            read_timeout=settings.MIKROTIK_READ_TIMEOUT)
        if not settings.MIKROTIK_HOTSPOT_SERVER:
            raise MikroTikConfigurationError('اسم خادم HotSpot الخاص بـ Hub غير مضبوط.')

    def test_connection(self):
        resource = self.client.system_resource()
        if not isinstance(resource, dict):
            raise MikroTikConnectionError('استجابة فحص MikroTik غير صالحة.')
        return True

    @staticmethod
    def username(entitlement):
        prefix = re.sub(r'[^A-Za-z0-9_.-]', '-', settings.MIKROTIK_USER_PREFIX)[:24]
        return f'{prefix}{entitlement.public_code.hex}'[:63]

    @staticmethod
    def ownership(entitlement): return f'hub-entitlement:{entitlement.pk}'

    def _fernet(self):
        try:
            from cryptography.fernet import Fernet
        except ImportError as exc:
            raise MikroTikConfigurationError('مكتبة تشفير بيانات دخول الشبكة غير مثبتة.') from exc
        if not settings.MIKROTIK_CREDENTIAL_KEY:
            raise MikroTikConfigurationError('مفتاح تشفير بيانات دخول الشبكة غير مضبوط.')
        try: return Fernet(settings.MIKROTIK_CREDENTIAL_KEY.encode())
        except (ValueError, TypeError) as exc:
            raise MikroTikConfigurationError('مفتاح تشفير بيانات دخول الشبكة غير صالح.') from exc

    def _credential(self, entitlement):
        from cryptography.fernet import InvalidToken
        cipher = self._fernet()
        if entitlement.network_credential_encrypted:
            try: return cipher.decrypt(entitlement.network_credential_encrypted.encode()).decode(), None
            except InvalidToken as exc:
                raise MikroTikConfigurationError('تعذر فك بيانات دخول الشبكة المخزنة.') from exc
        password = secrets.token_urlsafe(24)
        return password, cipher.encrypt(password.encode()).decode()

    def plan(self, entitlement, *, include_password=False):
        status = entitlement.effective_status()
        if status in {entitlement.Status.EXPIRED, entitlement.Status.CANCELLED}:
            raise ValidationError('لا يمكن تجهيز استحقاق منتهٍ أو ملغى.')
        profile = settings.MIKROTIK_DEFAULT_PROFILE
        if entitlement.bandwidth_profile_code:
            mapping = InternetBandwidthProfile.objects.filter(code=entitlement.bandwidth_profile_code).first()
            profile = mapping.router_profile_name if mapping and mapping.router_profile_name else profile
        if not profile:
            raise MikroTikConfigurationError('لا يوجد ملف RouterOS مطابق لملف السرعة.')
        from core.services.internet_access import get_effective_network_allowance
        safe_minutes = get_effective_network_allowance(
            entitlement, include_session_limit=False, include_reservations=True)
        values = {'name': self.username(entitlement), 'server': settings.MIKROTIK_HOTSPOT_SERVER,
                  'profile': profile, 'shared-users': str(entitlement.max_concurrent_devices),
                  'comment': self.ownership(entitlement), 'disabled': 'false'}
        if safe_minutes is not None: values['limit-uptime'] = str(timedelta(minutes=safe_minutes))
        devices = list(entitlement.devices.filter(is_active=True).values_list('device_mac', flat=True)[:2])
        if len(devices) == 1: values['mac-address'] = devices[0]
        if include_password: values['password'] = self._credential(entitlement)[0]
        return values

    def _owned_user(self, entitlement):
        user = self.client.find_hotspot_user(self.username(entitlement))
        if user and user.get('comment') != self.ownership(entitlement):
            raise MikroTikProvisioningError('تعارض اسم مستخدم RouterOS مع مورد لا يملكه Hub.')
        return user

    def _record_failure(self, entitlement, exc):
        entitlement.network_status = entitlement.NetworkStatus.PROVISION_ERROR
        entitlement.last_network_error = str(exc)[:500]
        entitlement.last_network_sync_at = timezone.now()
        entitlement.save(update_fields=['network_status', 'last_network_error', 'last_network_sync_at', 'updated_at'])

    def provision_access(self, entitlement):
        encrypted = None
        try:
            values = self.plan(entitlement)
            user = self._owned_user(entitlement)
            if not self.client.find_profile(values['profile']):
                raise MikroTikConfigurationError('ملف مستخدم RouterOS المحدد غير موجود.')
            if user:
                # A prior create may have succeeded remotely and timed out before Hub
                # persisted its encrypted credential. Reconcile the owned identity by
                # rotating to a newly persisted credential rather than creating again.
                if not entitlement.network_credential_encrypted:
                    password, encrypted = self._credential(entitlement)
                    values = {**values, 'password': password}
                self.client.update_hotspot_user(user['.id'], values)
            else:
                password, encrypted = self._credential(entitlement)
                self.client.create_hotspot_user({**values, 'password': password})
        except (MikroTikError, ValidationError) as exc:
            self._record_failure(entitlement, exc)
            raise
        entitlement.external_network_identifier = values['name']
        if encrypted: entitlement.network_credential_encrypted = encrypted
        entitlement.network_status = entitlement.NetworkStatus.PROVISIONED
        entitlement.last_network_error = ''
        entitlement.last_network_sync_at = timezone.now()
        entitlement.save(update_fields=['external_network_identifier', 'network_credential_encrypted', 'network_status', 'last_network_error', 'last_network_sync_at', 'updated_at'])
        return entitlement

    def refresh_access(self, entitlement):
        if entitlement.effective_status() in {entitlement.Status.EXPIRED, entitlement.Status.CANCELLED}:
            return self.disconnect_access(entitlement)
        return self.provision_access(entitlement)

    def disconnect_access(self, entitlement):
        try:
            user = self._owned_user(entitlement)
            if user: self.client.update_hotspot_user(user['.id'], {'disabled': 'true'})
            for session in self.client.active_sessions(self.username(entitlement)):
                if session.get('user') == self.username(entitlement): self.client.remove_active(session['.id'])
        except MikroTikError as exc:
            self._record_failure(entitlement, exc); raise
        entitlement.network_status = entitlement.NetworkStatus.DISCONNECTED
        entitlement.last_network_error = ''
        entitlement.last_network_sync_at = timezone.now()
        entitlement.save(update_fields=['network_status', 'last_network_error', 'last_network_sync_at', 'updated_at'])
        return entitlement

    def expire_access(self, entitlement): return self.disconnect_access(entitlement)


def get_network_backend(code='manual'):
    if code == 'mikrotik' and settings.MIKROTIK_ENABLED:
        return MikroTikNetworkBackend()
    return ManualNetworkBackend()
