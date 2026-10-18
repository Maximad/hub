"""Evidence-only internal consumption requests: deliberately no stock or ledger posting."""
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
            name='InternalConsumptionRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('business_date', models.DateField()),
                ('category', models.CharField(choices=[('hub_hospitality','ضيافة هَب'), ('owner_personal','استخدام شخصي للمالك'), ('staff','استهلاك الموظفين'), ('event','ضيافة فعالية أو ترويج')], max_length=24)),
                ('status', models.CharField(choices=[('submitted','مسجّل بانتظار المراجعة'), ('reviewed','تمت المراجعة (دون خصم)'), ('cancelled','ملغى (دون خصم)')], default='submitted', max_length=16)),
                ('entered_quantity', models.DecimalField(decimal_places=6, max_digits=12)),
                ('entered_unit', models.CharField(choices=[('kg','كغ'), ('g','غ'), ('liter','لتر'), ('ml','مل'), ('piece','قطعة'), ('pack','عبوة'), ('bottle','زجاجة'), ('box','صندوق'), ('portion','حصة'), ('other','أخرى')], max_length=20)),
                ('base_quantity', models.DecimalField(decimal_places=3, max_digits=12)),
                ('base_unit', models.CharField(choices=[('kg','كغ'), ('g','غ'), ('liter','لتر'), ('ml','مل'), ('piece','قطعة'), ('pack','عبوة'), ('bottle','زجاجة'), ('box','صندوق'), ('portion','حصة'), ('other','أخرى')], max_length=20)),
                ('estimated_unit_cost_syp_snapshot', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('purpose', models.CharField(max_length=255)),
                ('beneficiary_note', models.CharField(blank=True, max_length=120)),
                ('request_key', models.UUIDField(unique=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('review_note', models.CharField(blank=True, max_length=255)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='inventory_consumption_requests', to=settings.AUTH_USER_MODEL)),
                ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reviewed_consumption_requests', to=settings.AUTH_USER_MODEL)),
                ('inventory_item', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='consumption_requests', to='core.inventoryitem')),
            ],
            options={'ordering':['-created_at']},
        ),
        migrations.AddConstraint(model_name='internalconsumptionrequest', constraint=models.CheckConstraint(condition=Q(entered_quantity__gt=0),name='cons_req_entered_qty_gt_0')),
        migrations.AddConstraint(model_name='internalconsumptionrequest', constraint=models.CheckConstraint(condition=Q(base_quantity__gt=0),name='cons_req_base_qty_gt_0')),
    ]
