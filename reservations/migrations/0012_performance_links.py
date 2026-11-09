"""Bind existing reservations and public requests to each event's original performance."""
import django.db.models.deletion
from django.db import migrations, models


def attach_legacy_bookings(apps, schema_editor):
    Performance = apps.get_model('events', 'EventPerformance')
    Reservation = apps.get_model('reservations', 'Reservation')
    Request = apps.get_model('reservations', 'EventAttendanceRequest')
    for performance in Performance.objects.all().iterator():
        Reservation.objects.filter(event_id=performance.event_id, performance__isnull=True).update(
            performance_id=performance.pk)
        Request.objects.filter(event_id=performance.event_id, performance__isnull=True).update(
            performance_id=performance.pk, performance_uuid=performance.uuid)


class Migration(migrations.Migration):
    dependencies = [('events', '0009_event_performances'), ('reservations', '0011_reservation_ticket_order')]
    operations = [
        migrations.AddField(model_name='reservation', name='performance',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='reservations', to='events.eventperformance')),
        migrations.AddField(model_name='eventattendancerequest', name='performance',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='attendance_requests', to='events.eventperformance')),
        migrations.AddField(model_name='eventattendancerequest', name='performance_uuid',
            field=models.UUIDField(blank=True, editable=False, null=True)),
        migrations.RunPython(attach_legacy_bookings, migrations.RunPython.noop),
        migrations.RemoveConstraint(model_name='eventattendancerequest', name='one_open_attendance_request_per_browser'),
        migrations.AddConstraint(model_name='eventattendancerequest', constraint=models.UniqueConstraint(
            fields=('event', 'performance', 'owner_hash'),
            condition=models.Q(status__in=['pending', 'approved'], performance__isnull=False),
            name='one_open_request_per_performance')),
        migrations.AddConstraint(model_name='eventattendancerequest', constraint=models.UniqueConstraint(
            fields=('event', 'owner_hash'),
            condition=models.Q(status__in=['pending', 'approved'], performance__isnull=True),
            name='one_open_legacy_attendance_request')),
    ]
