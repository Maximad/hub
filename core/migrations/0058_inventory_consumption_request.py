"""Unposted, draft-only direct inventory consumption requests (D10 STILL BLOCKED)."""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0057_order_consumption'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='InventoryConsumptionRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('category', models.CharField(max_length=24, choices=[
                    ('hub_hospitality', 'ضيافة هَب'),
                    ('owner_personal', 'استخدام شخصي للمالك'),
                    ('staff', 'استهلاك الموظفين'),
                    ('event', 'ضيافة فعالية أو ترويج'),
                ])),
                ('state', models.CharField(max_length=20, default='pending_policy', choices=[
                    ('pending_policy', 'بانتظار السياسة والتسوية'),
                    ('cancelled', 'ملغى دون أثر على المخزون'),
                ])),
                ('business_date', models.DateField()),
                ('entered_quantity', models.DecimalField(max_digits=15, decimal_places=6)),
                ('entered_unit', models.CharField(max_length=20, choices=[
                    ('kg', 'كغ'), ('g', 'غ'), ('liter', 'لتر'), ('ml', 'مل'),
                    ('piece', 'قطعة'), ('pack', 'عبوة'), ('bottle', 'زجاجة'),
                    ('box', 'صندوق'), ('portion', 'حصة'), ('other', 'أخرى'),
                ])),
                ('base_quantity', models.DecimalField(max_digits=12, decimal_places=3)),
                ('base_unit', models.CharField(max_length=20, choices=[
                    ('kg', 'كغ'), ('g', 'غ'), ('liter', 'لتر'), ('ml', 'مل'),
                    ('piece', 'قطعة'), ('pack', 'عبوة'), ('bottle', 'زجاجة'),
                    ('box', 'صندوق'), ('portion', 'حصة'), ('other', 'أخرى'),
                ])),
                ('stock_quantity_at_request', models.DecimalField(max_digits=12, decimal_places=3)),
                ('unit_cost_estimate_syp', models.DecimalField(blank=True, null=True, max_digits=12, decimal_places=2)),
                ('total_cost_estimate_syp', models.DecimalField(blank=True, null=True, max_digits=16, decimal_places=2)),
                ('reason', models.CharField(max_length=255)),
                ('beneficiary_note', models.CharField(blank=True, max_length=120)),
                ('event_label', models.CharField(blank=True, max_length=120)),
                ('owner_treatment', models.CharField(blank=True, max_length=24, choices=[
                    ('draw_proposed', 'سحب شخصي (بانتظار سياسة التسوية)'),
                    ('reimburse_proposed', 'تعويض لاحق (بانتظار سياسة التسوية)'),
                ])),
                ('request_key', models.UUIDField(unique=True)),
                ('cancelled_at', models.DateTimeField(blank=True, null=True)),
                ('cancellation_reason', models.CharField(blank=True, max_length=255)),
                ('cancelled_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='cancelled_inventory_consumption_requests', to=settings.AUTH_USER_MODEL)),
                ('requested_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='inventory_consumption_requests', to=settings.AUTH_USER_MODEL)),
                ('inventory_item', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='consumption_requests', to='core.inventoryitem')),
            ],
            options={'ordering': ['-business_date', '-created_at']},
        ),
        migrations.AddConstraint(
            model_name='inventoryconsumptionrequest',
            constraint=models.CheckConstraint(condition=Q(base_quantity__gt=0), name='internal_use_request_quantity_positive'),
        ),
        migrations.AddConstraint(
            model_name='inventoryconsumptionrequest',
            constraint=models.CheckConstraint(condition=Q(entered_quantity__gt=0), name='internal_use_request_entered_positive'),
        ),
    ]
