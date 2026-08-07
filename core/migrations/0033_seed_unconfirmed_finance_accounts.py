from django.db import migrations


CANDIDATE_ACCOUNTS = (
    ('inventory:purchases', 'مخزون المشتريات', 'Purchase inventory', 'asset'),
)


def seed_inactive_candidates(apps, schema_editor):
    FinancialAccount = apps.get_model('core', 'FinancialAccount')
    for code, name_ar, name_en, account_type in CANDIDATE_ACCOUNTS:
        FinancialAccount.objects.get_or_create(
            code=code,
            defaults={
                'name_ar': name_ar,
                'name_en': name_en,
                'account_type': account_type,
                'scope': '',
                'business_unit': '',
                'is_active': False,
                'currency': 'SYP',
                'negative_balance_policy': 'forbid',
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0032_alter_cashmovement_financial_account'),
        ('core', '0031_systemsetting_posting_rollout_flags'),
    ]
    operations = [migrations.RunPython(seed_inactive_candidates, migrations.RunPython.noop)]
