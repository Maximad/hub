from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('internet', '0005_session_network_activated_at'),
    ]

    operations = [
        migrations.CreateModel(
            name='InternetOperationsState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(default='default', max_length=40, unique=True)),
                ('last_worker_seen_at', models.DateTimeField(blank=True, null=True)),
                ('last_lifecycle_at', models.DateTimeField(blank=True, null=True)),
                ('last_worker_summary', models.JSONField(blank=True, default=dict)),
                ('last_worker_error', models.CharField(blank=True, max_length=500)),
                ('last_mikrotik_check_at', models.DateTimeField(blank=True, null=True)),
                ('last_mikrotik_check_ok', models.BooleanField(blank=True, null=True)),
                ('last_mikrotik_check_message', models.CharField(blank=True, max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
