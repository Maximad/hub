"""Ticket orders link to reservations; no payments or product mappings are backfilled."""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('reservations', '0008_event_admission'),
        ('core', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ReservationTicketSale',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('booked_total_syp', models.PositiveIntegerField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='event_ticket_orders_created', to=settings.AUTH_USER_MODEL,
                )),
                ('order', models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='event_ticket_sale', to='core.order',
                )),
                ('reservation', models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='ticket_sale', to='reservations.reservation',
                )),
            ],
        ),
    ]
