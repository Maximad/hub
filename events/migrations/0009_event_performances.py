"""Date-specific performances; existing events retain their original show."""
import uuid
import django.db.models.deletion
from django.db import migrations, models


def seed_original_performances(apps, schema_editor):
    Event = apps.get_model('events', 'Event')
    Performance = apps.get_model('events', 'EventPerformance')
    Performance.objects.bulk_create([
        Performance(event_id=e.pk, uuid=uuid.uuid4(), starts_at=e.starts_at,
                    ends_at=e.ends_at, room_id=e.room_id, capacity=e.capacity or None)
        for e in Event.objects.all().iterator()
    ], batch_size=500)


class Migration(migrations.Migration):
    dependencies = [('events', '0008_venue_layout_revisions')]
    operations = [
        migrations.CreateModel(
            name='EventPerformance',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('starts_at', models.DateTimeField()),
                ('ends_at', models.DateTimeField(blank=True, null=True)),
                ('capacity', models.PositiveIntegerField(blank=True, null=True)),
                ('status', models.CharField(choices=[
                    ('scheduled', 'مجدول'), ('cancelled', 'ملغى'), ('completed', 'منتهٍ'),
                ], default='scheduled', max_length=16)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('event', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name='performances', to='events.event')),
                ('room', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='event_performances', to='core.room')),
            ],
            options={
                'ordering': ('starts_at', 'pk'),
                'constraints': [
                    models.UniqueConstraint(fields=('event', 'starts_at'), name='unique_event_performance_time'),
                    models.CheckConstraint(condition=models.Q(capacity__gt=0) | models.Q(capacity__isnull=True),
                                           name='event_performance_positive_capacity'),
                ],
            },
        ),
        migrations.RunPython(seed_original_performances, migrations.RunPython.noop),
    ]
