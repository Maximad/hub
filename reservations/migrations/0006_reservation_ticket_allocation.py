# Ticket allocations are not payments; existing reservations are untouched.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('reservations', '0005_eventattendancerequest'),
    ]

    operations = [
        migrations.AlterField(
            model_name='reservation', name='status',
            field=models.CharField(
                max_length=20, default='pending',
                choices=[
                    ('pending', 'بانتظار التأكيد'),
                    ('confirmed', 'مؤكد'),
                    ('cancelled', 'ملغى'),
                    ('completed', 'منتهٍ'),
                    ('no_show', 'لم يحضر'),
                ],
            ),
        ),
        migrations.CreateModel(
            name='ReservationTicketAllocation',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False, auto_created=True, verbose_name='ID')),
                ('quantity', models.PositiveIntegerField()),
                ('unit_price_syp', models.PositiveIntegerField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('reservation', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='ticket_allocations', to='reservations.reservation')),
                ('ticket_type', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='reservation_allocations', to='events.eventtickettype')),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(
                        fields=('reservation', 'ticket_type'), name='unique_reservation_ticket_tier'),
                    models.CheckConstraint(
                        condition=models.Q(quantity__gt=0), name='reservation_ticket_quantity_positive'),
                ],
            },
        ),
    ]
