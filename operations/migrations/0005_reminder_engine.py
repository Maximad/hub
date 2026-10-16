"""Add independent reminder schedules and append-only idempotent occurrences."""

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('operations', '0004_business_day_duty_coverage'),
        ('core', '0054_notificationevent_duty_routing_business_date'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Reminder',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title_ar', models.CharField(max_length=240)),
                ('details', models.TextField(blank=True)),
                ('status', models.CharField(max_length=12, choices=[('active', 'نشط'), ('completed', 'تم'), ('cancelled', 'ملغى')], default='active')),
                ('repeat', models.CharField(max_length=12, choices=[('once', 'مرة واحدة'), ('interval', 'كل فترة'), ('daily', 'يومياً'), ('weekly', 'أسبوعياً')], default='once')),
                ('interval_minutes', models.PositiveIntegerField(default=0)),
                ('next_due_at', models.DateTimeField(blank=True, null=True)),
                ('retry_after', models.DateTimeField(blank=True, null=True)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
                ('source_due_at', models.DateTimeField(blank=True, null=True)),
                ('responsibility', models.CharField(blank=True, choices=[
                    ('service', 'الخدمة'), ('cashier', 'الكاشير'), ('bar', 'مشاريب'),
                    ('kitchen', 'المطبخ'), ('operations', 'التشغيل'),
                ], max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('assigned_to', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assigned_reminders', to=settings.AUTH_USER_MODEL)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_reminders', to=settings.AUTH_USER_MODEL)),
                ('source_task', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='reminder_schedule', to='operations.businessdaytask')),
            ],
            options={'ordering': ['next_due_at', 'pk']},
        ),
        migrations.AddIndex(
            model_name='reminder',
            index=models.Index(fields=['status', 'next_due_at'], name='reminder_due_idx'),
        ),
        migrations.CreateModel(
            name='ReminderOccurrence',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('scheduled_for', models.DateTimeField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('notification_event', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='reminder_occurrences', to='core.notificationevent')),
                ('recipient_user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reminder_occurrences', to=settings.AUTH_USER_MODEL)),
                ('reminder', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='occurrences', to='operations.reminder')),
            ],
            options={
                'ordering': ['-scheduled_for', '-pk'],
                'constraints': [models.UniqueConstraint(fields=('reminder', 'scheduled_for'), name='unique_reminder_occurrence')],
            },
        ),
    ]
