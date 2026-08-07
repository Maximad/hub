from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0030_alter_cashmovement_amount_syp')]
    operations = [
        migrations.AddField(model_name='systemsetting', name='posting_ledger_writes_enabled', field=models.BooleanField(default=True, verbose_name='كتابة القيود المالية الجديدة')),
        migrations.AddField(model_name='systemsetting', name='posting_dual_read_enabled', field=models.BooleanField(default=False, verbose_name='مقارنة القراءات القديمة والجديدة')),
        migrations.AddField(model_name='systemsetting', name='posting_reports_enabled', field=models.BooleanField(default=False, verbose_name='استخدام القيود الجديدة في التقارير')),
    ]
