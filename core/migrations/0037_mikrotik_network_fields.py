from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0036_internetsession_allowance_minutes_consumed_and_more')]
    operations = [
        migrations.AddField(model_name='internetbandwidthprofile', name='router_profile_name', field=models.CharField(blank=True, max_length=120)),
        migrations.AddField(model_name='internetentitlement', name='network_credential_encrypted', field=models.TextField(blank=True, editable=False)),
    ]
