from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0046_operationsimportreceipt'),
        ('internet', '0006_internetoperationsstate'),
    ]

    operations = [
        migrations.CreateModel(
            name='InternetSessionBrowserBinding',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('credential', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='internet_session_bindings', to='core.hubvisitbrowsercredential')),
                ('session', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='browser_binding', to='core.internetsession')),
            ],
            options={
                'indexes': [models.Index(fields=['credential', 'created_at'], name='internet_browser_cred_idx')],
            },
        ),
    ]
