from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('internet', '0009_basic_wifi_allowance_policy'),
        ('core', '0044_hub_visits'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='InternalStaffInternetGrant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('entitlement', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='internal_staff_grant', to='core.internetentitlement')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='internal_internet_grants', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(
            model_name='internalstaffinternetgrant',
            index=models.Index(fields=['user', 'created_at'], name='staff_net_grant_user_idx'),
        ),
    ]
