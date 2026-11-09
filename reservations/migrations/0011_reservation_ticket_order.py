"""Add immutable reservation -> ticket POS order link; do not backfill money."""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('reservations', '0010_reservation_import_batch'),
        ('core', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ReservationTicketOrder',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('face_total_syp', models.PositiveIntegerField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='issued_event_ticket_orders',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('order', models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='reservation_ticket_order',
                    to='core.order',
                )),
                ('reservation', models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='ticket_order',
                    to='reservations.reservation',
                )),
            ],
        ),
    ]
