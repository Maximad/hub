import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='IntegrationToken',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120)),
                ('prefix', models.CharField(db_index=True, max_length=24, unique=True)),
                ('secret_digest', models.CharField(editable=False, max_length=64)),
                ('scopes', models.JSONField(blank=True, default=list)),
                ('is_active', models.BooleanField(default=True)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
                ('last_used_at', models.DateTimeField(blank=True, null=True)),
                ('notes', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_integration_tokens', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Management integration token',
                'verbose_name_plural': 'Management integration tokens',
                'ordering': ['name', 'prefix'],
            },
        ),
        migrations.CreateModel(
            name='IntegrationMutationApproval',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nonce', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('operation', models.CharField(max_length=80)),
                ('payload_digest', models.CharField(editable=False, max_length=64)),
                ('expires_at', models.DateTimeField()),
                ('consumed_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('token', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='mutation_approvals', to='audit.integrationtoken')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='IntegrationRequestLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('request_id', models.UUIDField(db_index=True)),
                ('method', models.CharField(max_length=12)),
                ('path', models.CharField(max_length=255)),
                ('scope', models.CharField(blank=True, max_length=80)),
                ('status_code', models.PositiveSmallIntegerField()),
                ('remote_addr', models.GenericIPAddressField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('token', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='request_logs', to='audit.integrationtoken')),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
