"""Opt-in source-linked operational follow-up; no historical data backfill."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('operations', '0005_reminder_engine')]

    operations = [
        migrations.CreateModel(
            name='OperationalReviewCase',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source_type', models.CharField(max_length=16, choices=[('inventory', 'مادة مخزون'), ('product', 'منتج')])),
                ('source_id', models.PositiveBigIntegerField()),
                ('issue_code', models.CharField(max_length=32, choices=[('needs_count', 'يلزم جرد فعلي'), ('recipe_review', 'مراجعة الوصفة'), ('price_review', 'مراجعة سعر البيع')])),
                ('status', models.CharField(max_length=12, choices=[('open', 'بانتظار الاستكمال'), ('resolved', 'مكتملة بالدليل')], default='open')),
                ('source_label', models.CharField(max_length=180)),
                ('evidence', models.JSONField(default=dict, blank=True)),
                ('opened_at', models.DateTimeField(auto_now_add=True)),
                ('reviewed_at', models.DateTimeField(auto_now=True)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'ordering': ['status', '-reviewed_at', 'pk'],
                'constraints': [
                    models.UniqueConstraint(fields=('source_type', 'source_id', 'issue_code'), name='unique_operational_review_source_issue')
                ],
                'indexes': [
                    models.Index(fields=('status', 'issue_code'), name='ops_review_open_issue_idx')
                ],
            },
        ),
    ]
