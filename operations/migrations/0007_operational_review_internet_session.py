"""Add opt-in internet-session incident category; no data backfill."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('operations', '0006_operational_review_case')]

    operations = [
        migrations.AlterField(
            model_name='operationalreviewcase',
            name='source_type',
            field=models.CharField(max_length=16, choices=[
                ('inventory', 'مادة مخزون'),
                ('product', 'منتج'),
                ('internet_session', 'جلسة إنترنت'),
            ]),
        ),
        migrations.AlterField(
            model_name='operationalreviewcase',
            name='issue_code',
            field=models.CharField(max_length=32, choices=[
                ('needs_count', 'يلزم جرد فعلي'),
                ('recipe_review', 'مراجعة الوصفة'),
                ('price_review', 'مراجعة سعر البيع'),
                ('network_review', 'مراجعة فشل تفعيل الإنترنت'),
            ]),
        ),
    ]
