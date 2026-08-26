from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('internet', '0004_session_network_state'),
    ]

    operations = [
        migrations.AddField(
            model_name='internetsessionnetworkstate',
            name='network_activated_at',
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
    ]
