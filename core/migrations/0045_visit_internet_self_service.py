from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('core', '0044_hub_visits')]
    operations = [
        migrations.AddField(
            model_name='systemsetting', name='customer_internet_self_service_enabled',
            field=models.BooleanField(default=False, verbose_name='تفعيل خدمة الإنترنت الذاتية للزبائن'),
        ),
        migrations.AddField(
            model_name='internetsession', name='visit',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='internet_sessions', to='core.hubvisit'),
        ),
    ]
