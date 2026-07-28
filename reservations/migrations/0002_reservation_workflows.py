from django.db import migrations, models
import django.db.models.deletion


def classify_reservations(apps, schema_editor):
    Reservation = apps.get_model('reservations', 'Reservation')
    Reservation.objects.filter(event_id__isnull=False).update(reservation_type='event', reservation_date=None, start_time=None, end_time=None)
    Reservation.objects.filter(event_id__isnull=True).update(reservation_type='regular')
    for reservation in Reservation.objects.filter(event_id__isnull=True, table_area_id__isnull=False).select_related('table_area').iterator():
        reservation.room_id = reservation.table_area.room_id
        reservation.save(update_fields=['room'])


class Migration(migrations.Migration):
    dependencies = [('core', '0001_initial'), ('reservations', '0001_initial')]
    operations = [
        migrations.AddField(model_name='reservation', name='room', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reservations', to='core.room')),
        migrations.AlterField(model_name='reservation', name='reservation_date', field=models.DateField(blank=True, null=True)),
        migrations.AlterField(model_name='reservation', name='start_time', field=models.TimeField(blank=True, null=True)),
        migrations.AlterField(model_name='reservation', name='reservation_type', field=models.CharField(choices=[('regular', 'حجز عادي'), ('event', 'حجز فعالية')], default='regular', max_length=30)),
        migrations.RunPython(classify_reservations, migrations.RunPython.noop),
    ]
