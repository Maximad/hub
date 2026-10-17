"""Stage unapproved public USD rate observations and signed local adjustment.

These tables do not migrate existing prices or finance postings.
"""
import django.db.models.deletion
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0055_operational_reminder_event'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='LocalExchangeRatePolicy',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('sweida_offset_syp', models.DecimalField(decimal_places=2, default=0, max_digits=8)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='ImportedMarketRate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('source_fingerprint', models.CharField(max_length=64, unique=True)),
                ('raw_sha256', models.CharField(max_length=64)),
                ('source_timestamp', models.DateTimeField()),
                ('reference_buy_syp', models.DecimalField(decimal_places=2, max_digits=12)),
                ('reference_sell_syp', models.DecimalField(decimal_places=2, max_digits=12)),
                ('business_date', models.DateField()),
                ('status', models.CharField(choices=[('pending', 'بانتظار المراجعة'), ('approved', 'معتمد'), ('rejected', 'مرفوض')], default='pending', max_length=12)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('applied_offset_syp', models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True)),
                ('review_note', models.CharField(blank=True, max_length=500)),
                ('approved_exchange_rate', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='core.exchangerate')),
                ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-source_timestamp', '-created_at']},
        ),
    ]
