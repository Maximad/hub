from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('internet', '0012_staff_internet_observed_uptime')]

    operations = [
        migrations.CreateModel(
            name='StaffInternetDeviceEvidence',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('device_token', models.CharField(max_length=64)),
                ('first_seen_at', models.DateTimeField()),
                ('last_seen_at', models.DateTimeField(db_index=True)),
                ('last_active_uptime_seconds', models.PositiveIntegerField(blank=True, null=True)),
                ('grant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='observed_devices', to='internet.internalstaffinternetgrant')),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(fields=('grant', 'device_token'), name='unique_staff_wifi_device_evidence'),
                ],
            },
        ),
    ]
