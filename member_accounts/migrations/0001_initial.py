import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def create_existing_accounts(apps, schema_editor):
    Member = apps.get_model('core', 'Member')
    MemberAccount = apps.get_model('member_accounts', 'MemberAccount')
    batch = [MemberAccount(member_id=member_id) for member_id in Member.objects.values_list('pk', flat=True)]
    if batch:
        MemberAccount.objects.bulk_create(batch, ignore_conflicts=True)


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0002_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='MemberAccount',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('active', 'Active'), ('locked', 'Locked')], default='pending', max_length=20)),
                ('email', models.EmailField(blank=True, max_length=254, null=True, unique=True)),
                ('claimed_at', models.DateTimeField(blank=True, null=True)),
                ('phone_verified_at', models.DateTimeField(blank=True, null=True)),
                ('email_verified_at', models.DateTimeField(blank=True, null=True)),
                ('last_login_at', models.DateTimeField(blank=True, null=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('member', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='login_account', to='core.member')),
            ],
        ),
        migrations.CreateModel(
            name='MemberInvitation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('invited_phone', models.CharField(blank=True, max_length=30)),
                ('invited_name', models.CharField(blank=True, max_length=120)),
                ('purpose', models.CharField(choices=[('account_claim', 'Account claim'), ('add_device', 'Add trusted device')], default='account_claim', max_length=20)),
                ('token_hash', models.CharField(editable=False, max_length=64, unique=True)),
                ('expires_at', models.DateTimeField()),
                ('claimed_at', models.DateTimeField(blank=True, null=True)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('claimed_member', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='claimed_account_invitations', to='core.member')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_member_invitations', to=settings.AUTH_USER_MODEL)),
                ('target_member', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='account_invitations', to='core.member')),
            ],
            options={'ordering': ('-created_at',)},
        ),
        migrations.AddIndex(
            model_name='memberinvitation',
            index=models.Index(fields=['token_hash'], name='member_invite_token_idx'),
        ),
        migrations.AddIndex(
            model_name='memberinvitation',
            index=models.Index(fields=['expires_at'], name='member_invite_expiry_idx'),
        ),
        migrations.RunPython(create_existing_accounts, migrations.RunPython.noop),
    ]
