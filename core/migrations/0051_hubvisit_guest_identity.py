from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0050_alter_internetnetworkoperation_operation'),
    ]

    operations = [
        migrations.AddField(
            model_name='hubvisit',
            name='guest_name',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='hubvisit',
            name='guest_phone',
            field=models.CharField(blank=True, max_length=30),
        ),
    ]
