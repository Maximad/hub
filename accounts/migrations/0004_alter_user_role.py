from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0003_alter_user_role'),
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
                    ('internet_provider', 'مزوّد الإنترنت'),
                ],
                default='waiter',
                max_length=20,
            ),
        ),
    ]
