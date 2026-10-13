from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('internet', '0013_staffinternetdeviceevidence')]

    operations = [
        migrations.CreateModel(
            name='StaffInternetObservedInterval',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('start_at', models.DateTimeField()),
                ('end_at', models.DateTimeField(db_index=True)),
                ('observed_seconds', models.PositiveSmallIntegerField()),
                ('device', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sampled_intervals', to='internet.staffinternetdeviceevidence')),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(fields=('device', 'end_at'), name='unique_staff_wifi_sampled_interval'),
                ],
            },
        ),
    ]
