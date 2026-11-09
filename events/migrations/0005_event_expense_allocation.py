"""Event allocation references existing Expense; no monetary records are created."""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('events', '0004_event_accepts_attendance_requests_and_more'),
        ('core', '0015_expensecategory_expense_cashmovement'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='EventExpenseAllocation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount_syp', models.PositiveIntegerField()),
                ('rationale', models.CharField(max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('allocated_by', models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='event_expense_allocations_made', to=settings.AUTH_USER_MODEL,
                )),
                ('event', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='expense_allocations', to='events.event',
                )),
                ('expense', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='event_allocations', to='core.expense',
                )),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(
                        fields=('event', 'expense'),
                        name='uniq_event_expense_assignment',
                    ),
                    models.CheckConstraint(
                        condition=models.Q(amount_syp__gt=0),
                        name='event_expense_amount_positive',
                    ),
                ],
            },
        ),
    ]
