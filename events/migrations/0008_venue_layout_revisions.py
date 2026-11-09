"""Retain historical copies so staff can restore a layout draft safely."""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def create_baseline_revisions(apps, schema_editor):
    Template = apps.get_model('events', 'VenueLayoutTemplate')
    EventLayout = apps.get_model('events', 'EventLayoutSnapshot')
    Revision = apps.get_model('events', 'VenueLayoutRevision')
    for template in Template.objects.all().iterator():
        Revision.objects.create(
            template=template, version=template.version, layout=template.layout,
            created_by_id=template.updated_by_id or template.created_by_id,
        )
    for event_layout in EventLayout.objects.all().iterator():
        Revision.objects.create(
            event_layout=event_layout, version=event_layout.version,
            layout=event_layout.layout, created_by_id=event_layout.updated_by_id,
        )


class Migration(migrations.Migration):
    dependencies = [
        ('events', '0007_seed_unapproved_layout_templates'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='VenueLayoutRevision',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.PositiveIntegerField()),
                ('layout', models.JSONField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='venue_layout_revisions_created', to=settings.AUTH_USER_MODEL,
                )),
                ('event_layout', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                    related_name='revisions', to='events.eventlayoutsnapshot',
                )),
                ('template', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                    related_name='revisions', to='events.venuelayouttemplate',
                )),
            ],
            options={'constraints': [
                models.CheckConstraint(
                    condition=(models.Q(template__isnull=False, event_layout__isnull=True) |
                               models.Q(template__isnull=True, event_layout__isnull=False)),
                    name='layout_revision_one_owner',
                ),
                models.UniqueConstraint(
                    fields=('template', 'version'), name='unique_venue_template_revision',
                ),
                models.UniqueConstraint(
                    fields=('event_layout', 'version'), name='unique_venue_event_revision',
                ),
            ]},
        ),
        migrations.RunPython(create_baseline_revisions, reverse_code=migrations.RunPython.noop),
    ]
