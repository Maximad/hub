from django.db import migrations, models
import django.db.models.deletion


def copy_event_rooms(apps, schema_editor):
    Event = apps.get_model('events', 'Event')
    for event in Event.objects.select_related('location_area__room').iterator():
        if event.location_area_id:
            event.room_id = event.location_area.room_id
            event.save(update_fields=['room'])


class Migration(migrations.Migration):
    dependencies = [('events', '0002_eventmedia')]
    operations = [
        migrations.AddField(model_name='event', name='room', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='events', to='core.room')),
        migrations.RunPython(copy_event_rooms, migrations.RunPython.noop),
        migrations.RemoveField(model_name='event', name='location_area'),
    ]
