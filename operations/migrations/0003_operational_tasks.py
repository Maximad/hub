from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('operations', '0002_opening_handover'),
    ]

    operations = [
        migrations.CreateModel(
            name='OperationalTaskTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title_ar', models.CharField(max_length=240)),
                ('details', models.TextField(blank=True)),
                ('weekdays', models.JSONField(blank=True, default=list, help_text='0=Monday ... 6=Sunday; empty means every day')),
                ('due_time', models.TimeField(blank=True, null=True)),
                ('priority', models.CharField(choices=[('normal', 'عادية'), ('high', 'مهمة')], default='normal', max_length=12)),
                ('responsibility_role', models.CharField(blank=True, max_length=40)),
                ('is_required', models.BooleanField(default=False)),
                ('is_active', models.BooleanField(default=True)),
                ('active_from', models.DateField(blank=True, null=True)),
                ('active_until', models.DateField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_operational_task_templates', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['title_ar', 'pk']},
        ),
        migrations.CreateModel(
            name='BusinessDayTask',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fingerprint', models.CharField(max_length=180)),
                ('kind', models.CharField(choices=[('recurring', 'مهمة دورية'), ('event', 'تحضير فعالية'), ('reservation', 'تحضير حجز'), ('inventory', 'مخزون/شراء'), ('manual', 'مهمة يدوية')], default='manual', max_length=20)),
                ('title_ar', models.CharField(max_length=240)),
                ('details', models.TextField(blank=True)),
                ('priority', models.CharField(choices=[('normal', 'عادية'), ('high', 'مهمة')], default='normal', max_length=12)),
                ('status', models.CharField(choices=[('pending', 'مفتوحة'), ('done', 'تمت'), ('waived', 'أُغلقت بسبب مسجّل')], default='pending', max_length=20)),
                ('due_at', models.DateTimeField(blank=True, null=True)),
                ('is_required', models.BooleanField(default=False)),
                ('responsibility_role', models.CharField(blank=True, max_length=40)),
                ('source_type', models.CharField(blank=True, max_length=40)),
                ('source_id', models.CharField(blank=True, max_length=80)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('completion_note', models.TextField(blank=True)),
                ('last_reminded_at', models.DateTimeField(blank=True, null=True)),
                ('reminder_count', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('assigned_to', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assigned_business_day_tasks', to=settings.AUTH_USER_MODEL)),
                ('business_day', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tasks', to='operations.businessday')),
                ('completed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='completed_business_day_tasks', to=settings.AUTH_USER_MODEL)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_business_day_tasks', to=settings.AUTH_USER_MODEL)),
                ('task_template', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='generated_tasks', to='operations.operationaltasktemplate')),
            ],
            options={'ordering': ['status', 'due_at', '-priority', 'created_at']},
        ),
        migrations.AddConstraint(
            model_name='businessdaytask',
            constraint=models.UniqueConstraint(fields=('business_day', 'fingerprint'), name='unique_business_day_task_fingerprint'),
        ),
        migrations.AddIndex(
            model_name='businessdaytask',
            index=models.Index(fields=['business_day', 'status', 'due_at'], name='business_day_task_due_idx'),
        ),
    ]
