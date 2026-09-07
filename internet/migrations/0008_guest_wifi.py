from django.db import migrations, models
import django.db.models.deletion


def seed_default_policy(apps, schema_editor):
    GuestWifiPolicy = apps.get_model('internet', 'GuestWifiPolicy')
    GuestWifiPolicy.objects.get_or_create(key='default')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0048_sync_product_channel_flags'),
        ('internet', '0007_internetsessionbrowserbinding'),
    ]

    operations = [
        migrations.CreateModel(
            name='GuestWifiCodeAttempt',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fingerprint_hash', models.CharField(editable=False, max_length=64, unique=True)),
                ('window_started_at', models.DateTimeField()),
                ('attempt_count', models.PositiveSmallIntegerField(default=0)),
                ('blocked_until', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name='GuestWifiPolicy',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(default='default', max_length=40, unique=True)),
                ('enabled', models.BooleanField(default=False)),
                ('require_venue_code', models.BooleanField(default=True)),
                ('member_bypass_venue_code', models.BooleanField(default=True)),
                ('session_minutes', models.PositiveSmallIntegerField(default=120)),
                ('max_sessions_per_day', models.PositiveSmallIntegerField(default=1)),
                ('code_rotation_minutes', models.PositiveSmallIntegerField(default=240)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('bandwidth_profile', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='guest_wifi_policies', to='core.internetbandwidthprofile')),
            ],
        ),
        migrations.CreateModel(
            name='GuestWifiGrant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('business_date', models.DateField(db_index=True)),
                ('code_slot', models.BigIntegerField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('credential', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='guest_wifi_grants', to='core.hubvisitbrowsercredential')),
                ('session', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='guest_wifi_grant', to='core.internetsession')),
            ],
            options={
                'indexes': [models.Index(fields=['credential', 'business_date'], name='guest_wifi_cred_day_idx')],
            },
        ),
        migrations.RunPython(seed_default_policy, migrations.RunPython.noop),
    ]
