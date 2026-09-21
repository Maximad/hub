from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0049_migrate_internet_partner_users_to_provider_role'),
    ]

    operations = [
        migrations.AlterField(
            model_name='internetnetworkoperation',
            name='operation',
            field=models.CharField(
                choices=[
                    ('provision', 'Provision'),
                    ('refresh', 'Refresh'),
                    ('deauthenticate', 'Deauthenticate active sessions'),
                    ('disconnect', 'Disconnect'),
                    ('expire', 'Expire'),
                ],
                max_length=20,
            ),
        ),
    ]
