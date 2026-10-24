# Generated for Hub Suite fast Internet approval timeout.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0060_fast_internet_staff_approval'),
    ]

    operations = [
        migrations.AlterField(
            model_name='hubvisitbrowsercredential',
            name='fast_internet_approval',
            field=models.CharField(
                choices=[
                    ('not_requested', 'لم يُطلب'),
                    ('pending', 'بانتظار موافقة الموظف'),
                    ('approved', 'موافق عليه'),
                    ('rejected', 'لم تتم الموافقة'),
                    ('expired', 'انتهت مهلة الموافقة'),
                ],
                db_default='not_requested', default='not_requested', max_length=16,
            ),
        ),
        migrations.AddField(
            model_name='systemsetting',
            name='fast_internet_approval_timeout_minutes',
            field=models.PositiveSmallIntegerField(
                db_default=30, default=30,
                verbose_name='مهلة طلب موافقة الإنترنت السريع بالدقائق',
            ),
        ),
    ]
