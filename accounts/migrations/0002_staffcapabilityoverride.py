from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='StaffCapabilityOverride',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('capability', models.CharField(max_length=80)),
                ('allowed', models.BooleanField()),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'user',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='staff_capability_overrides',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'ordering': ['user_id', 'capability'],
            },
        ),
        migrations.AddConstraint(
            model_name='staffcapabilityoverride',
            constraint=models.UniqueConstraint(
                fields=('user', 'capability'),
                name='unique_staff_capability_override',
            ),
        ),
    ]
