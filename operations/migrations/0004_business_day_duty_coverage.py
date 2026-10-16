"""Add a non-routing operational duty-coverage layer to the existing roster."""

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('operations', '0003_operational_tasks'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='BusinessDayDutyCoverage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('responsibility', models.CharField(choices=[
                    ('service', 'الخدمة'), ('cashier', 'الكاشير'),
                    ('bar', 'مشاريب'), ('kitchen', 'المطبخ'),
                    ('operations', 'التشغيل'),
                ], max_length=20)),
                ('proposed_at', models.DateTimeField(blank=True, null=True)),
                ('transferred_at', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('business_day', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='duty_coverages', to='operations.businessday',
                )),
                ('primary_assignment', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name='primary_duty_coverages', to='operations.businessdaystaffassignment',
                )),
                ('proposed_assignment', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name='proposed_duty_coverages', to='operations.businessdaystaffassignment',
                )),
                ('proposed_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='requested_duty_handovers', to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['responsibility'],
                'constraints': [models.UniqueConstraint(
                    fields=('business_day', 'responsibility'),
                    name='unique_business_day_duty_coverage',
                )],
            },
        ),
    ]
