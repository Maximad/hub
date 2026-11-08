# Booking collection arrangements do not create payments or financial journals.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('reservations', '0006_reservation_ticket_allocation'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ReservationPaymentArrangement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('mode', models.CharField(
                    max_length=24, choices=[
                        ('pay_at_door', 'الدفع عند الدخول'),
                        ('staff_guarantee', 'مؤكد بضمان الإدارة'),
                    ],
                )),
                ('due_at', models.DateTimeField(blank=True, null=True)),
                ('reason', models.CharField(blank=True, max_length=250)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('reservation', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='payment_arrangement', to='reservations.reservation',
                )),
                ('guaranteed_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name='guaranteed_reservations', to=settings.AUTH_USER_MODEL,
                )),
                ('approved_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='approved_reservation_arrangements', to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'constraints': [
                    models.CheckConstraint(
                        condition=(
                            models.Q(mode='pay_at_door', guaranteed_by__isnull=True)
                            | models.Q(mode='staff_guarantee', guaranteed_by__isnull=False)
                        ),
                        name='reservation_arrangement_guarantee_consistent',
                    ),
                ],
            },
        ),
    ]
