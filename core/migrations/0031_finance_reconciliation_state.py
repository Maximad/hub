from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0030_alter_cashmovement_amount_syp'), ('core', '0029_remove_dailyclose_unique_finalized_daily_close_per_date_and_more')]
    operations = [
        migrations.CreateModel(
            name='FinanceReconciliationState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
                ('operation', models.CharField(max_length=80)), ('record_type', models.CharField(max_length=80)),
                ('record_id', models.CharField(max_length=80)), ('status', models.CharField(default='completed', max_length=20)),
                ('details', models.JSONField(blank=True, default=dict)),
            ],
        ),
        migrations.CreateModel(
            name='FinanceReviewItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
                ('issue_code', models.CharField(max_length=80)), ('record_type', models.CharField(max_length=80)),
                ('record_id', models.CharField(max_length=80)), ('reason', models.TextField()),
                ('details', models.JSONField(blank=True, default=dict)), ('resolved_at', models.DateTimeField(blank=True, null=True)),
            ],
        ),
        migrations.AddConstraint(model_name='financereconciliationstate', constraint=models.UniqueConstraint(fields=('operation', 'record_type', 'record_id'), name='unique_finance_reconciliation_step')),
        migrations.AddConstraint(model_name='financereviewitem', constraint=models.UniqueConstraint(fields=('issue_code', 'record_type', 'record_id'), name='unique_open_finance_review_item')),
    ]
