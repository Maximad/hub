"""Reusable draft SVG layouts and event-specific snapshots; no seat booking."""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('events', '0005_event_expense_allocation'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='VenueLayoutTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120)),
                ('kind', models.CharField(choices=[
                    ('comedy', 'كوميديا وطاولات'),
                    ('theatre', 'مسرح وسينما'),
                    ('cabaret', 'موسيقى وكباريه'),
                    ('workshop', 'ورشات وحوارات'),
                    ('standing', 'حفلة ووقوف'),
                    ('custom', 'تصميم مخصص'),
                ], default='comedy', max_length=20)),
                ('description', models.CharField(blank=True, max_length=300)),
                ('layout', models.JSONField(default=dict)),
                ('version', models.PositiveIntegerField(default=1)),
                ('archived', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='venue_templates_created', to=settings.AUTH_USER_MODEL,
                )),
                ('updated_by', models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='venue_templates_updated', to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'ordering': ['kind', 'name', 'pk']},
        ),
        migrations.CreateModel(
            name='EventLayoutSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('layout', models.JSONField(default=dict)),
                ('version', models.PositiveIntegerField(default=1)),
                ('assigned_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('assigned_by', models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='venue_layouts_assigned', to=settings.AUTH_USER_MODEL,
                )),
                ('updated_by', models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='venue_event_layouts_edited', to=settings.AUTH_USER_MODEL,
                )),
                ('event', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='seating_layout', to='events.event',
                )),
                ('template', models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='event_copies', to='events.venuelayouttemplate',
                )),
            ],
        ),
    ]
