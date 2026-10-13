"""Allow existing full-admin access profiles to manage private staff internet.

The migration is additive and never alters active entitlements or network state.
It intentionally does not grant the capability to cashier or provider profiles.
"""
from django.db import migrations


def grant_to_default_admin_profile(apps, schema_editor):
    Profile = apps.get_model('accounts', 'AccessProfile')
    for profile in Profile.objects.filter(code='full-admin', base_role='admin', is_system=True):
        current = list(profile.capabilities or [])
        if 'internal_internet_manage' not in current:
            current.append('internal_internet_manage')
            Profile.objects.filter(pk=profile.pk).update(capabilities=current)


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0005_accessprofile_user_access_profile_user_job_title'),
    ]

    operations = [
        migrations.RunPython(grant_to_default_admin_profile, migrations.RunPython.noop),
    ]
