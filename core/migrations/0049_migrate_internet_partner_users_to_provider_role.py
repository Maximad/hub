from django.db import migrations


def migrate_partner_users(apps, schema_editor):
    InternetPartnerUser = apps.get_model('core', 'InternetPartnerUser')
    User = apps.get_model('accounts', 'User')
    user_ids = InternetPartnerUser.objects.values_list('user_id', flat=True).distinct()
    # Historical portal-only accounts were created with the default waiter role.
    # Do not silently strip a deliberate cashier/admin role from a dual-purpose user.
    User.objects.filter(pk__in=user_ids, role='waiter', is_superuser=False).update(
        role='internet_provider',
        is_staff=False,
    )


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0004_alter_user_role'),
        ('core', '0048_sync_product_channel_flags'),
    ]

    operations = [
        migrations.RunPython(migrate_partner_users, migrations.RunPython.noop),
    ]
