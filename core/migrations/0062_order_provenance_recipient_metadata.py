# Additive nonfinancial origin and recipient metadata for orders.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0061_fast_internet_approval_expiry'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='order', name='entered_by',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='entered_orders', to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='order', name='entry_source',
            field=models.CharField(
                choices=[
                    ('unknown', 'غير موثق'),
                    ('staff_pos', 'نقطة البيع (الموظف)'),
                    ('public_menu', 'طلب ذاتي من القائمة'),
                    ('system', 'إنشاء آلي'),
                ],
                db_default='unknown', default='unknown', max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='order', name='recipient_kind',
            field=models.CharField(
                choices=[
                    ('customer', 'زبون'),
                    ('neighbor', 'جار هَبّ'),
                    ('staff', 'أحد أفراد الفريق'),
                ],
                db_default='customer', default='customer', max_length=12,
            ),
        ),
        migrations.AddField(
            model_name='order', name='recipient_note',
            field=models.CharField(blank=True, max_length=120),
        ),
    ]
