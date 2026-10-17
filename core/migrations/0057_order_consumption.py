"""Classify complimentary POS items without creating direct inventory or ledger postings."""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0056_imported_market_rate_policy'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='OrderConsumption',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('category', models.CharField(choices=[('hub_hospitality', 'ضيافة هَب'), ('owner_personal', 'استخدام شخصي للمالك')], max_length=24)),
                ('owner_treatment', models.CharField(blank=True, choices=[('draw_proposed', 'سحب شخصي (بانتظار سياسة التسوية)'), ('reimburse_proposed', 'تعويض لاحق (بانتظار سياسة التسوية)')], max_length=24)),
                ('quantity', models.PositiveIntegerField()),
                ('retail_value_syp', models.PositiveIntegerField()),
                ('estimated_cost_syp', models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ('reason', models.CharField(max_length=255)),
                ('beneficiary_note', models.CharField(blank=True, max_length=120)),
                ('business_date', models.DateField()),
                ('request_key', models.CharField(max_length=100, unique=True)),
                ('approved_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='approved_order_consumptions', to=settings.AUTH_USER_MODEL)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='recorded_order_consumptions', to=settings.AUTH_USER_MODEL)),
                ('discount', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='consumption_record', to='core.orderdiscount')),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='consumptions', to='core.order')),
                ('order_item', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='consumptions', to='core.orderitem')),
            ],
            options={'ordering': ['-business_date', '-created_at']},
        ),
        migrations.AddConstraint(
            model_name='orderconsumption',
            constraint=models.CheckConstraint(condition=Q(quantity__gt=0), name='order_consumption_qty_positive'),
        ),
        migrations.AddConstraint(
            model_name='orderconsumption',
            constraint=models.CheckConstraint(condition=Q(retail_value_syp__gt=0), name='order_consumption_retail_positive'),
        ),
    ]
