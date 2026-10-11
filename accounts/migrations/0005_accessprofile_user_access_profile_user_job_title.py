from django.db import migrations, models
import django.db.models.deletion


CAPABILITIES = [
    'staff_home',
    'orders',
    'pos',
    'cashier',
    'reports',
    'finance',
    'inventory',
    'reservations',
    'events',
    'settings',
    'imports',
    'users',
    'modifiers',
    'internet_billing',
    'members/internet',
    'kitchen_board',
    'partial_payment_approval',
    'order_edit',
    'visit_manage',
    'delivery_management',
    'provider_dashboard',
    'internet_view_members',
    'internet_manage_members',
    'internet_view_sessions',
    'internet_manage_sessions',
    'internet_view_subscriptions',
    'internet_manage_subscriptions',
    'internet_view_packages',
    'internet_manage_packages',
    'internet_view_network',
    'internet_manage_network',
    'internet_view_reports',
    'internet_manage_provider_settings',
]

PROFILE_DEFINITIONS = {
    'admin': {
        'code': 'full-admin',
        'name_ar': 'إدارة كاملة',
        'description': 'وصول إداري كامل إلى جميع وحدات النظام.',
        'capabilities': CAPABILITIES,
    },
    'cashier': {
        'code': 'cashier-operations',
        'name_ar': 'تشغيل وكاشير',
        'description': 'نقطة البيع والتحصيل والعمليات اليومية.',
        'capabilities': [
            'staff_home', 'orders', 'pos', 'cashier', 'finance', 'inventory',
            'internet_billing', 'members/internet', 'kitchen_board',
            'order_edit', 'visit_manage', 'delivery_management',
        ],
    },
    'waiter': {
        'code': 'service-operations',
        'name_ar': 'خدمة وتنظيم',
        'description': 'خدمة الزبائن والطلبات والحجوزات والفعاليات.',
        'capabilities': [
            'staff_home', 'orders', 'pos', 'reservations', 'events',
            'order_edit', 'visit_manage', 'delivery_management',
        ],
    },
    'kitchen': {
        'code': 'prep-operations',
        'name_ar': 'تحضير وتشغيل خلفي',
        'description': 'التحضير والمخزون وإدارة الطلبات والجلسات.',
        'capabilities': [
            'staff_home', 'inventory', 'kitchen_board',
            'order_edit', 'visit_manage',
        ],
    },
    'bartender': {
        'code': 'bar-prep',
        'name_ar': 'بار وتحضير',
        'description': 'تشغيل البار والتحضير والمخزون.',
        'capabilities': [
            'staff_home', 'inventory', 'kitchen_board',
            'order_edit', 'visit_manage',
        ],
    },
    'internet_provider': {
        'code': 'internet-provider',
        'name_ar': 'مزوّد إنترنت خارجي',
        'description': 'بوابة مزوّد الإنترنت وإدارة بياناته فقط.',
        'capabilities': [
            'provider_dashboard',
            'internet_view_members', 'internet_manage_members',
            'internet_view_sessions', 'internet_manage_sessions',
            'internet_view_subscriptions', 'internet_manage_subscriptions',
            'internet_view_packages', 'internet_manage_packages',
            'internet_view_network', 'internet_manage_network',
            'internet_view_reports', 'internet_manage_provider_settings',
        ],
    },
    'staff': {
        'code': 'general-staff',
        'name_ar': 'فريق عام',
        'description': 'دخول أساسي لمساحة العمل. أضف الصلاحيات المطلوبة حسب الوظيفة.',
        'capabilities': ['staff_home'],
    },
}


def seed_access_profiles(apps, schema_editor):
    AccessProfile = apps.get_model('accounts', 'AccessProfile')
    User = apps.get_model('accounts', 'User')

    profiles = {}
    for base_role, definition in PROFILE_DEFINITIONS.items():
        profile, _ = AccessProfile.objects.update_or_create(
            code=definition['code'],
            defaults={
                'name_ar': definition['name_ar'],
                'description': definition['description'],
                'base_role': base_role,
                'capabilities': list(definition['capabilities']),
                'is_active': True,
                'is_system': True,
            },
        )
        profiles[base_role] = profile

    for user in User.objects.all().iterator():
        profile = profiles['admin'] if user.is_superuser else (profiles.get(user.role) or profiles['staff'])
        User.objects.filter(pk=user.pk).update(access_profile_id=profile.pk)


def unseed_access_profiles(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    AccessProfile = apps.get_model('accounts', 'AccessProfile')
    User.objects.update(access_profile_id=None)
    AccessProfile.objects.filter(
        code__in=[definition['code'] for definition in PROFILE_DEFINITIONS.values()]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0004_alter_user_role'),
    ]

    operations = [
        migrations.CreateModel(
            name='AccessProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.SlugField(max_length=64, unique=True)),
                ('name_ar', models.CharField(max_length=120)),
                ('description', models.TextField(blank=True)),
                ('base_role', models.CharField(
                    choices=[
                        ('staff', 'فريق عام'),
                        ('admin', 'إدارة'),
                        ('cashier', 'تشغيل وكاشير'),
                        ('waiter', 'خدمة وتنظيم'),
                        ('kitchen', 'تحضير وتشغيل خلفي'),
                        ('bartender', 'بار وتحضير'),
                        ('internet_provider', 'مزوّد إنترنت خارجي'),
                    ],
                    default='staff',
                    max_length=20,
                )),
                ('capabilities', models.JSONField(blank=True, default=list)),
                ('is_active', models.BooleanField(default=True)),
                ('is_system', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ('name_ar', 'code'), 'verbose_name': 'نمط وصول', 'verbose_name_plural': 'أنماط الوصول'},
        ),
        migrations.AddField(
            model_name='user',
            name='access_profile',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='users',
                to='accounts.accessprofile',
                verbose_name='نمط الوصول',
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='job_title',
            field=models.CharField(blank=True, max_length=120, verbose_name='المسمى الوظيفي'),
        ),
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(
                choices=[
                    ('staff', 'فريق عام'),
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
        migrations.RunPython(seed_access_profiles, unseed_access_profiles),
    ]
