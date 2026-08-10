"""Network adapters. Manual is the safe default and makes no external calls."""
from django.conf import settings
from django.utils import timezone


class ManualNetworkBackend:
    code = 'manual'
    def provision_access(self, entitlement):
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
    """Explicit extension point; physical RouterOS integration is deferred."""
    code = 'mikrotik'
    def __init__(self, *args, **kwargs):
        raise NotImplementedError('MikroTik integration is not enabled in this release.')


def get_network_backend(code='manual'):
    if code == 'mikrotik' and settings.MIKROTIK_ENABLED:
        return MikroTikNetworkBackend()
    return ManualNetworkBackend()
