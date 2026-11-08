# Admission is independent of restaurant visits and financial posting.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('reservations', '0007_reservation_payment_arrangement'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='EventAdmission',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('arrived_count', models.PositiveIntegerField()),
                ('first_recorded_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('reservation', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='event_admission',
                    to='reservations.reservation',
                )),
                ('recorded_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='event_admissions_recorded',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'constraints': [
                    models.CheckConstraint(
                        condition=models.Q(arrived_count__gt=0),
                        name='event_admission_positive_attendees',
                    ),
                ],
            },
        ),
    ]
