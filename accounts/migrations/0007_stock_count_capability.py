"""Add stock-count capability only to the existing operational profiles.

Preserve custom capabilities and per-user allow/deny overrides. Broad inventory
access (e.g. kitchen) must not imply permission to confirm a stock count.
"""
from django.db import migrations


def seed_authorized_profiles(apps, schema_editor):
    Profile = apps.get_model('accounts', 'AccessProfile')
    profiles = Profile.objects.filter(
        code__in=['full-admin', 'cashier-operations'],
        is_system=True,
    )
    for profile in profiles.iterator():
        if (profile.code == 'full-admin' and profile.base_role != 'admin'
                or profile.code == 'cashier-operations' and profile.base_role != 'cashier'):
            continue
        capabilities = list(profile.capabilities or [])
        if 'inventory_stock_count' not in capabilities:
            capabilities.append('inventory_stock_count')
            Profile.objects.filter(pk=profile.pk).update(capabilities=capabilities)


class Migration(migrations.Migration):
    dependencies = [('accounts', '0006_internal_internet_manage_capability')]

    operations = [migrations.RunPython(
        seed_authorized_profiles, migrations.RunPython.noop,
    )]
