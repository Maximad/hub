"""Typed network policy for InternetPackage without duplicating commercial fields."""

from django.core.exceptions import ValidationError


MANUAL = 'manual'
MIKROTIK = 'mikrotik'
SUPPORTED_BACKENDS = {MANUAL, MIKROTIK}


def package_network_backend(package):
    """Resolve the backend snapshot for a new entitlement.

    `InternetPackage.backend_config` is already the package-level network adapter
    configuration.  `network_backend` is the one typed key interpreted by Hub;
    existing packages remain Manual unless explicitly opted into MikroTik.
    """
    config = package.backend_config or {}
    if not isinstance(config, dict):
        raise ValidationError({'backend_config': 'إعدادات شبكة الباقة يجب أن تكون كائناً صالحاً.'})
    value = str(config.get('network_backend') or MANUAL).strip().lower()
    if value not in SUPPORTED_BACKENDS:
        raise ValidationError({
            'backend_config': 'network_backend يجب أن يكون manual أو mikrotik.'
        })
    return value
