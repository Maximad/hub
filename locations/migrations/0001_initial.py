from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0046_operationsimportreceipt'),
    ]

    operations = [
        migrations.CreateModel(
            name='TableAreaSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('customer_entry_code', models.CharField(blank=True, help_text='الرقم الذي يدخله الزبون يدوياً. مستقل عن اسم الطاولة ورمز QR.', max_length=6, null=True, unique=True, verbose_name='رقم الطاولة للزبون')),
                ('staff_description', models.TextField(blank=True, help_text='معلومة داخلية عن موقع أو شكل الطاولة. لا تظهر للزبون.', verbose_name='وصف داخلي للموظفين')),
                ('table', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='access_settings', to='core.tablearea', verbose_name='الطاولة')),
            ],
            options={
                'verbose_name': 'إعدادات الطاولة',
                'verbose_name_plural': 'إعدادات الطاولات',
                'ordering': ('customer_entry_code', 'table_id'),
            },
        ),
    ]
