"""Add an optional routing marker, without modifying historical events."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0053_notification_read_receipt'),
    ]

    operations = [
        migrations.AddField(
            model_name='notificationevent',
            name='duty_routing_business_date',
            field=models.DateField(blank=True, db_index=True, null=True),
        ),
    ]
