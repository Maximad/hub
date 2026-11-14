from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('reservations', '0012_performance_links'),
        ('member_accounts', '0002_memberloginchallenge'),
    ]
    operations = [migrations.AddField(
        model_name='eventattendancerequest', name='account',
        field=models.ForeignKey(blank=True, editable=False, null=True,
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name='attendance_requests', to='member_accounts.memberaccount'),
    )]
