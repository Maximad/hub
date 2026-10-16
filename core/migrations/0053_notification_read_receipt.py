"""Add independent read receipts for role and direct staff notifications."""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0052_alter_notificationevent_event_type'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='NotificationReadReceipt',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('read_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('notification_recipient', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='user_read_receipts',
                    to='core.notificationrecipient',
                )),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='notification_read_receipts',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(
                        fields=('notification_recipient', 'user'),
                        name='unique_notification_user_read_receipt',
                    )
                ]
            },
        ),
    ]
