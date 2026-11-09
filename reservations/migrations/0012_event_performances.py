"""Link historical event bookings and attendance requests to their original show."""
import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


def backfill(apps, schema_editor):
    Performance = apps.get_model('events', 'EventPerformance')
    Reservation = apps.get_model('reservations', 'Reservation')
    Request = apps.get_model('reservations', 'EventAttendanceRequest')
    db = schema_editor.connection.alias
    for performance in Performance.objects.using(db).all().iterator():
        Reservation.objects.using(db).filter(event_id=performance.event_id, reservation_type='event', performance__isnull=True).update(performance_id=performance.pk)
        Request.objects.using(db).filter(event_id=performance.event_id, performance__isnull=True).update(performance_id=performance.pk)


class Migration(migrations.Migration):
    dependencies = [
        ('reservations', '0011_reservation_ticket_order'),
        ('events', '0009_event_performances'),
    ]
    operations = [
        migrations.AddField(model_name='reservation', name='performance',
            field=models.ForeignKey(to='events.eventperformance', related_name='reservations',
                null=True, blank=True, on_delete=django.db.models.deletion.PROTECT)),
        migrations.AddField(model_name='eventattendancerequest', name='performance',
            field=models.ForeignKey(to='events.eventperformance', related_name='attendance_requests',
                null=True, on_delete=django.db.models.deletion.SET_NULL)),
        migrations.RunPython(backfill, reverse_code=migrations.RunPython.noop),
        migrations.RemoveConstraint(model_name='eventattendancerequest', name='one_open_attendance_request_per_browser'),
        migrations.AddConstraint(model_name='eventattendancerequest',
            constraint=models.UniqueConstraint(fields=['performance', 'owner_hash'], condition=Q(status__in=['pending', 'approved']),
                name='one_open_request_per_performance_browser')),
    ]
