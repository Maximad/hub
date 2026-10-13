from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('internet', '0010_internalstaffinternetgrant')]

    operations = [
        migrations.CreateModel(
            name='StaffInternetConnectionSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('state', models.CharField(choices=[('observed', 'تم الفحص'), ('missing', 'لم يُعثر على حساب شبكة'), ('error', 'تعذر الفحص')], default='error', max_length=16)),
                ('active_login_count', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('distinct_device_count', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('checked_at', models.DateTimeField(blank=True, null=True)),
                ('last_success_at', models.DateTimeField(blank=True, null=True)),
                ('error_code', models.CharField(blank=True, max_length=40)),
                ('grant', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='connection_snapshot', to='internet.internalstaffinternetgrant')),
            ],
        ),
    ]
