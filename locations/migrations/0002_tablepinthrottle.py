from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('locations', '0001_initial')]
    operations = [migrations.CreateModel(
        name='TablePINThrottle',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('failures', models.PositiveIntegerField(default=0)),
            ('window_started_at', models.DateTimeField()),
            ('locked_until', models.DateTimeField(blank=True, null=True)),
            ('table', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                          related_name='pin_throttle', to='core.tablearea')),
        ],
    )]
