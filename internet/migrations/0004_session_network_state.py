from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0046_operationsimportreceipt'),
        ('internet', '0003_internetcatalogbinding'),
    ]

    operations = [
        migrations.CreateModel(
            name='InternetSessionNetworkState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('network_credential_encrypted', models.TextField(blank=True, editable=False)),
                ('last_network_sync_at', models.DateTimeField(blank=True, null=True)),
                ('last_network_error', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('session', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='network_state', to='core.internetsession')),
            ],
        ),
        migrations.CreateModel(
            name='InternetSessionNetworkOperation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('operation', models.CharField(choices=[('provision', 'Provision'), ('refresh', 'Refresh'), ('disconnect', 'Disconnect')], max_length=20)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('processing', 'Processing'), ('succeeded', 'Succeeded'), ('failed', 'Failed')], default='pending', max_length=20)),
                ('idempotency_key', models.CharField(max_length=180, unique=True)),
                ('reason', models.CharField(blank=True, max_length=200)),
                ('attempt_count', models.PositiveIntegerField(default=0)),
                ('last_attempt_at', models.DateTimeField(blank=True, null=True)),
                ('next_attempt_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('last_error', models.CharField(blank=True, max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='session_network_operations', to='core.internetsession')),
            ],
            options={
                'indexes': [models.Index(fields=['status', 'next_attempt_at'], name='internet_sess_netop_ready_idx')],
            },
        ),
    ]
