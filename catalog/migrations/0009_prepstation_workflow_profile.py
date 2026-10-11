"""Opt-in station preparation workflows. Legacy stations remain Team mode."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('catalog', '0008_productmedia_display_on_pos_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='prepstation',
            name='workflow_profile',
            field=models.CharField(
                choices=[
                    ('direct', 'مباشر · موظف واحد'),
                    ('team', 'فريق · مراحل التحضير والتسليم'),
                    ('event', 'فعالية · مراحل مفصلة'),
                ],
                default='team',
                help_text='المباشر يسمح بإنهاء تقديم العنصر بخطوة واحدة. الفريق والفعاليات يحتفظان بالمراحل المفصلة.',
                max_length=12,
                verbose_name='أسلوب التحضير',
            ),
        ),
    ]
