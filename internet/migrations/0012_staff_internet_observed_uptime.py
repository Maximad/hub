from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('internet', '0011_staffinternetconnectionsnapshot')]

    operations = [
        migrations.AddField(
            model_name='staffinternetconnectionsnapshot',
            name='longest_active_uptime_seconds',
            field=models.PositiveIntegerField(null=True, blank=True),
        ),
    ]
