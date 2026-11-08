# Links existing posted POS payments to event reservations; no financial posting.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('reservations', '0008_event_admission'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ReservationTicketPaymentLink',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('linked_at', models.DateTimeField(auto_now_add=True)),
                ('payment', models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='event_ticket_booking_link', to='core.payment',
                )),
                ('reservation', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='ticket_payment_links', to='reservations.reservation',
                )),
                ('linked_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='linked_ticket_payments', to=settings.AUTH_USER_MODEL,
                )),
            ],
        ),
    ]
