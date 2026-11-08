# Ticket-only cashier orders are created explicitly by staff; no payment is posted.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('reservations', '0010_reservation_import_record'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ReservationTicketSale',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('reservation', models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='ticket_sale', to='reservations.reservation',
                )),
                ('order', models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='event_ticket_sale', to='core.order',
                )),
                ('created_by', models.ForeignKey(
                    null=True, blank=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='created_reservation_ticket_sales',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
        ),
    ]
