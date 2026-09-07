from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('internet', '0008_guest_wifi'),
    ]

    operations = [
        migrations.AddField(
            model_name='guestwifipolicy',
            name='daily_complimentary_minutes',
            field=models.PositiveSmallIntegerField(
                default=360,
                help_text='يشمل الرصيد الأولي ومكافآت الطلبات والمنح اليدوية، ولا يقيّد الإنترنت المدفوع أو الاستحقاقات.',
                verbose_name='السقف المجاني اليومي بالدقائق',
            ),
        ),
        migrations.AddField(
            model_name='guestwifipolicy',
            name='order_bonus_enabled',
            field=models.BooleanField(default=True, verbose_name='تمديد الإنترنت الأساسي بعد طلب مؤهل'),
        ),
        migrations.AddField(
            model_name='guestwifipolicy',
            name='order_bonus_minutes',
            field=models.PositiveSmallIntegerField(default=120, verbose_name='دقائق المكافأة لكل طلب مؤهل'),
        ),
        migrations.AddField(
            model_name='guestwifipolicy',
            name='qualifying_order_minimum_syp',
            field=models.PositiveBigIntegerField(
                default=0,
                help_text='صفر يعني أن أي طلب مقبول بقيمة أكبر من صفر مؤهل.',
                verbose_name='الحد الأدنى لقيمة الطلب المؤهل',
            ),
        ),
        migrations.AlterField(
            model_name='guestwifipolicy',
            name='enabled',
            field=models.BooleanField(default=False, verbose_name='تفعيل الإنترنت الأساسي'),
        ),
        migrations.AlterField(
            model_name='guestwifipolicy',
            name='require_venue_code',
            field=models.BooleanField(default=True, verbose_name='طلب رمز المكان عند أول اتصال يومي'),
        ),
        migrations.AlterField(
            model_name='guestwifipolicy',
            name='member_bypass_venue_code',
            field=models.BooleanField(default=True, verbose_name='الحساب ذو الاشتراك الفعال يتجاوز رمز المكان'),
        ),
        migrations.AlterField(
            model_name='guestwifipolicy',
            name='session_minutes',
            field=models.PositiveSmallIntegerField(
                default=120,
                help_text='يُمنح مرة واحدة للجهاز في يوم العمل عند أول تفعيل للإنترنت الأساسي.',
                verbose_name='الرصيد المجاني الأولي بالدقائق',
            ),
        ),
        migrations.AlterField(
            model_name='guestwifipolicy',
            name='max_sessions_per_day',
            field=models.PositiveSmallIntegerField(
                default=1,
                help_text='حقل توافق قديم؛ السياسة الحالية تعتمد سقف الدقائق اليومي بدلاً من عدد الجلسات.',
            ),
        ),
        migrations.AlterField(
            model_name='guestwifipolicy',
            name='code_rotation_minutes',
            field=models.PositiveSmallIntegerField(default=240, verbose_name='تغيير رمز المكان كل (دقيقة)'),
        ),
        migrations.AlterField(
            model_name='guestwifipolicy',
            name='bandwidth_profile',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='guest_wifi_policies',
                to='core.internetbandwidthprofile',
                verbose_name='ملف سرعة الإنترنت الأساسي',
            ),
        ),
        migrations.CreateModel(
            name='GuestWifiDailyAllowance',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('business_date', models.DateField(db_index=True)),
                ('initial_minutes_granted', models.PositiveSmallIntegerField(default=0)),
                ('order_bonus_minutes_granted', models.PositiveSmallIntegerField(default=0)),
                ('manual_bonus_minutes_granted', models.PositiveSmallIntegerField(default=0)),
                ('consumed_seconds', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('credential', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='guest_wifi_daily_allowances', to='core.hubvisitbrowsercredential')),
            ],
            options={
                'indexes': [models.Index(fields=['business_date', 'credential'], name='guest_wifi_allow_day_idx')],
                'constraints': [models.UniqueConstraint(fields=('credential', 'business_date'), name='uniq_guest_wifi_allowance_per_day')],
            },
        ),
        migrations.AddField(
            model_name='guestwifigrant',
            name='allowance',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='sessions', to='internet.guestwifidailyallowance'),
        ),
        migrations.AddField(
            model_name='guestwifigrant',
            name='usage_accounted_at',
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.CreateModel(
            name='GuestWifiOrderBonus',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('business_date', models.DateField(db_index=True)),
                ('minutes', models.PositiveSmallIntegerField()),
                ('order_total_syp_snapshot', models.PositiveBigIntegerField(default=0)),
                ('applied_at', models.DateTimeField(blank=True, null=True)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('allowance', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='order_bonuses', to='internet.guestwifidailyallowance')),
                ('applied_session', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='guest_wifi_applied_order_bonuses', to='core.internetsession')),
                ('credential', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='guest_wifi_order_bonuses', to='core.hubvisitbrowsercredential')),
                ('order', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='guest_wifi_order_bonus', to='core.order')),
            ],
            options={
                'indexes': [models.Index(fields=['credential', 'business_date', 'revoked_at'], name='guest_wifi_bonus_day_idx')],
            },
        ),
    ]
