from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='BusinessDay',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('business_date', models.DateField(unique=True, verbose_name='تاريخ العمل')),
                ('status', models.CharField(choices=[('open', 'مفتوح'), ('closing', 'قيد الإغلاق'), ('closed', 'مغلق'), ('reopened', 'أعيد فتحه')], default='open', max_length=20)),
                ('cutoff_hour', models.PositiveSmallIntegerField(default=4, verbose_name='ساعة نهاية يوم العمل')),
                ('opened_at', models.DateTimeField(blank=True, null=True)),
                ('closing_started_at', models.DateTimeField(blank=True, null=True)),
                ('closed_at', models.DateTimeField(blank=True, null=True)),
                ('reopened_at', models.DateTimeField(blank=True, null=True)),
                ('reopen_reason', models.TextField(blank=True)),
                ('notes', models.TextField(blank=True)),
                ('code_hash', models.CharField(blank=True, editable=False, max_length=256)),
                ('code_ciphertext', models.TextField(blank=True, editable=False)),
                ('code_version', models.PositiveIntegerField(default=0, editable=False)),
                ('code_issued_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('closing_snapshot', models.JSONField(blank=True, default=dict, editable=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('closed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='closed_business_days', to=settings.AUTH_USER_MODEL)),
                ('opened_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='opened_business_days', to=settings.AUTH_USER_MODEL)),
                ('reopened_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reopened_business_days', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-business_date'],
                'permissions': [('manage_business_day', 'Can manage business day'), ('view_daily_staff_code', 'Can view daily staff code'), ('rotate_daily_staff_code', 'Can rotate daily staff code')],
            },
        ),
        migrations.CreateModel(
            name='BusinessDayException',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fingerprint', models.CharField(max_length=180)),
                ('category', models.CharField(choices=[('order', 'طلبات'), ('internet', 'إنترنت'), ('visit', 'زيارات'), ('shift', 'مناوبات'), ('finance', 'مالية'), ('system', 'نظام')], max_length=20)),
                ('severity', models.CharField(choices=[('info', 'معلومة'), ('warning', 'تحذير'), ('blocker', 'مانع للإغلاق')], default='warning', max_length=20)),
                ('status', models.CharField(choices=[('open', 'مفتوح'), ('resolved', 'تم الحل'), ('carried_forward', 'رُحّل')], default='open', max_length=20)),
                ('object_type', models.CharField(blank=True, max_length=80)),
                ('object_id', models.CharField(blank=True, max_length=80)),
                ('title_ar', models.CharField(max_length=240)),
                ('details', models.JSONField(blank=True, default=dict)),
                ('resolution_note', models.TextField(blank=True)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('business_day', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='exceptions', to='operations.businessday')),
                ('resolved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='resolved_business_day_exceptions', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-severity', 'category', 'created_at']},
        ),
        migrations.CreateModel(
            name='StaffDailyCodeReceipt',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code_version', models.PositiveIntegerField(default=1)),
                ('notified_at', models.DateTimeField(blank=True, null=True)),
                ('viewed_at', models.DateTimeField(blank=True, null=True)),
                ('first_used_at', models.DateTimeField(blank=True, null=True)),
                ('last_used_at', models.DateTimeField(blank=True, null=True)),
                ('use_count', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('business_day', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='code_receipts', to='operations.businessday')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='daily_code_receipts', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['user_id']},
        ),
        migrations.AddConstraint(
            model_name='businessday',
            constraint=models.CheckConstraint(condition=models.Q(cutoff_hour__gte=0, cutoff_hour__lte=23), name='business_day_cutoff_hour_range'),
        ),
        migrations.AddConstraint(
            model_name='businessdayexception',
            constraint=models.UniqueConstraint(fields=('business_day', 'fingerprint'), name='unique_business_day_exception_fingerprint'),
        ),
        migrations.AddConstraint(
            model_name='staffdailycodereceipt',
            constraint=models.UniqueConstraint(fields=('business_day', 'user'), name='unique_daily_code_receipt_per_user'),
        ),
    ]
