from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('core', '0045_visit_internet_self_service')]
    operations = [
        migrations.CreateModel(
            name='OperationsImportReceipt',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('import_key', models.CharField(max_length=160, unique=True)),
                ('kind', models.CharField(default='purchase', max_length=40)),
                ('intent', models.JSONField(default=dict)),
                ('purchase', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='operations_import_receipt', to='core.purchase')),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
