from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_usercapabilityoverride'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(
                choices=[
                    ('admin', 'مدير'),
                    ('cashier', 'كاشير'),
                    ('waiter', 'نادل'),
                    ('kitchen', 'مطبخ'),
                    ('bartender', 'بار'),
                ],
                default='waiter',
                max_length=20,
            ),
        ),
    ]
