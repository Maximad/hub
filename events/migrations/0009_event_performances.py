"""One occurrence per existing event, preserving original booked schedules."""
import django.db.models.deletion
from django.db import migrations, models


def backfill(apps, schema_editor):
    Event = apps.get_model('events', 'Event')
    Performance = apps.get_model('events', 'EventPerformance')
    for event in Event.objects.using(schema_editor.connection.alias).all().iterator():
        Performance.objects.using(schema_editor.connection.alias).create(
            event_id=event.pk, starts_at=event.starts_at, ends_at=event.ends_at,
            room_id=event.room_id, capacity=event.capacity,
        )


class Migration(migrations.Migration):
    dependencies = [('events', '0008_venue_layout_revisions')]
    operations = [
        migrations.CreateModel(name='EventPerformance', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('starts_at', models.DateTimeField()),
            ('ends_at', models.DateTimeField(null=True, blank=True)),
            ('capacity', models.PositiveIntegerField(null=True, blank=True)),
            ('status', models.CharField(max_length=20, default='scheduled', choices=[('scheduled', 'مجدول'), ('cancelled', 'ملغى'), ('completed', 'منتهٍ')])),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('event', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='performances', to='events.event')),
            ('room', models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL, to='core.room')),
        ], options={'ordering': ('starts_at', 'pk'), 'constraints': [
            models.UniqueConstraint(fields=('event', 'starts_at'), name='uniq_event_performance_start'),
        ]}),
        migrations.RunPython(backfill, reverse_code=migrations.RunPython.noop),
    ]
