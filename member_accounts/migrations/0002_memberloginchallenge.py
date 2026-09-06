import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('member_accounts', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='MemberLoginChallenge',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('phone_hash', models.CharField(db_index=True, max_length=64)),
                ('code_hash', models.CharField(editable=False, max_length=64)),
                ('expires_at', models.DateTimeField()),
                ('consumed_at', models.DateTimeField(blank=True, null=True)),
                ('attempts', models.PositiveSmallIntegerField(default=0)),
                ('max_attempts', models.PositiveSmallIntegerField(default=5)),
                ('delivery_status', models.CharField(choices=[('pending', 'Pending'), ('sent', 'Sent'), ('skipped', 'Skipped'), ('failed', 'Failed')], default='pending', max_length=16)),
                ('requested_ip_hash', models.CharField(blank=True, db_index=True, max_length=64)),
                ('next_path', models.CharField(blank=True, max_length=500)),
                ('user_agent', models.CharField(blank=True, max_length=160)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('member', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='login_challenges', to='core.member')),
            ],
            options={'ordering': ('-created_at',)},
        ),
        migrations.AddIndex(
            model_name='memberloginchallenge',
            index=models.Index(fields=['member', 'created_at'], name='member_login_member_idx'),
        ),
        migrations.AddIndex(
            model_name='memberloginchallenge',
            index=models.Index(fields=['expires_at'], name='member_login_expiry_idx'),
        ),
    ]
