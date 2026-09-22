from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('operations', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='businessday',
            name='opening_completed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='businessday',
            name='opening_last_reminded_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='businessday',
            name='opening_completed_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='completed_business_day_openings', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='staffdailycodereceipt',
            name='last_reminded_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='staffdailycodereceipt',
            name='reminder_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.CreateModel(
            name='BusinessDayStaffAssignment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role_snapshot', models.CharField(blank=True, max_length=40)),
                ('note', models.CharField(blank=True, max_length=240)),
                ('assigned_at', models.DateTimeField(auto_now_add=True)),
                ('checked_in_at', models.DateTimeField(blank=True, null=True)),
                ('assigned_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assigned_business_day_staff', to=settings.AUTH_USER_MODEL)),
                ('business_day', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='staff_assignments', to='operations.businessday')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='business_day_assignments', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['user_id']},
        ),
        migrations.CreateModel(
            name='BusinessDayChecklistItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('stage', models.CharField(choices=[('opening', 'الافتتاح')], default='opening', max_length=20)),
                ('code', models.CharField(max_length=80)),
                ('label_ar', models.CharField(max_length=240)),
                ('is_required', models.BooleanField(default=True)),
                ('sort_order', models.PositiveSmallIntegerField(default=0)),
                ('status', models.CharField(choices=[('pending', 'بانتظار التأكيد'), ('done', 'تم'), ('waived', 'تم التجاوز بسبب مسجّل')], default='pending', max_length=20)),
                ('note', models.TextField(blank=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('business_day', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='checklist_items', to='operations.businessday')),
                ('completed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='completed_business_day_checklist_items', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['stage', 'sort_order', 'pk']},
        ),
        migrations.CreateModel(
            name='HandoverNote',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('message', models.TextField()),
                ('priority', models.CharField(choices=[('normal', 'عادية'), ('high', 'مهمة')], default='normal', max_length=12)),
                ('status', models.CharField(choices=[('open', 'تحتاج متابعة'), ('acknowledged', 'تم الاطلاع'), ('resolved', 'تم الحل')], default='open', max_length=20)),
                ('acknowledged_at', models.DateTimeField(blank=True, null=True)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('acknowledged_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='acknowledged_handover_notes', to=settings.AUTH_USER_MODEL)),
                ('assigned_to', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assigned_handover_notes', to=settings.AUTH_USER_MODEL)),
                ('business_day', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='handover_notes', to='operations.businessday')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_handover_notes', to=settings.AUTH_USER_MODEL)),
                ('resolved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='resolved_handover_notes', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['status', '-priority', '-created_at']},
        ),
        migrations.AddConstraint(
            model_name='businessdaystaffassignment',
            constraint=models.UniqueConstraint(fields=('business_day', 'user'), name='unique_business_day_staff_assignment'),
        ),
        migrations.AddConstraint(
            model_name='businessdaychecklistitem',
            constraint=models.UniqueConstraint(fields=('business_day', 'stage', 'code'), name='unique_business_day_checklist_code'),
        ),
    ]
