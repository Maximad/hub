"""Import metadata only; no customer data is added to this table."""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('reservations', '0009_reservation_ticket_payment_link'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ReservationImportBatch',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fingerprint', models.CharField(max_length=64)),
                ('imported_count', models.PositiveIntegerField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='event_booking_imports', to=settings.AUTH_USER_MODEL,
                )),
                ('event', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='reservation_import_batches', to='events.event',
                )),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(
                        fields=('event', 'fingerprint'),
                        name='unique_event_booking_import_batch',
                    ),
                ],
            },
        ),
    ]
