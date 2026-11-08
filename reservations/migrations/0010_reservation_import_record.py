# Stable non-PII provenance keys for safe idempotent reservation imports.
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
            name='ReservationImportRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source', models.CharField(max_length=40)),
                ('external_key', models.CharField(max_length=80)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('event', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='reservation_import_records', to='events.event',
                )),
                ('reservation', models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='import_provenance', to='reservations.reservation',
                )),
                ('imported_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(
                        fields=('event', 'source', 'external_key'),
                        name='unique_event_reservation_source_reference',
                    ),
                ],
            },
        ),
    ]
