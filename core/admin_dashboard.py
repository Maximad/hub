from django.contrib import admin
from django.urls import NoReverseMatch, reverse

HIDDEN_ADMIN_MODELS = {
    ('core', 'activitylog'),
    ('core', 'notificationevent'),
    ('core', 'notificationrecipient'),
    ('core', 'notificationlog'),
    ('core', 'orderitem'),
    ('core', 'purchaseitem'),
    ('core', 'stockmovement'),
    ('core', 'productionbatchingredient'),
    ('core', 'orderdiscount'),
    ('catalog', 'productmedia'),
    ('events', 'eventmedia'),
    ('vendors', 'vendormedia'),
    ('members', 'membercreditledger'),
}

APP_LABELS_AR = {
    'accounts': 'الحسابات',
    'auth': 'المصادقة والصلاحيات',
    'catalog': 'المنيو والوسائط',
    'core': 'التشغيل الأساسي',
    'events': 'الفعاليات',
    'internet': 'الإنترنت',
    'members': 'الأعضاء والاشتراكات',
    'reservations': 'الحجوزات',
    'vendors': 'البائعون و Food Lab',
}

MODEL_LABELS_AR = {
    ('accounts', 'user'): 'المستخدمون',
    ('auth', 'group'): 'مجموعات الصلاحيات',
    ('catalog', 'menusection'): 'أقسام المنيو',
    ('catalog', 'productavailability'): 'قواعد توفر المنتجات',
    ('catalog', 'tag'): 'الوسوم',
    ('catalog', 'productoption'): 'خيارات المنتجات',
    ('catalog', 'productoptiongroup'): 'مجموعات خيارات المنتجات',
    ('catalog', 'prepstation'): 'محطات التحضير',
    ('catalog', 'mediaasset'): 'مكتبة الوسائط',
    ('catalog', 'productmedia'): 'وسائط المنتجات',
    ('core', 'cashmovement'): 'حركات الصندوق',
    ('core', 'category'): 'التصنيفات',
    ('core', 'dailyclose'): 'إغلاقات الأيام',
    ('core', 'expensecategory'): 'فئات المصروفات',
    ('core', 'expense'): 'المصروفات',
    ('core', 'internetpackage'): 'باقات الإنترنت',
    ('core', 'internetsession'): 'جلسات الإنترنت',
    ('core', 'member'): 'الأعضاء',
    ('core', 'notificationpreference'): 'تفضيلات الإشعارات',
    ('core', 'order'): 'الطلبات',
    ('core', 'payment'): 'الدفعات',
    ('core', 'product'): 'المنتجات',
    ('core', 'room'): 'الغرف والمساحات',
    ('core', 'shift'): 'الورديات',
    ('core', 'tablearea'): 'مناطق الطاولات',
    ('core', 'systemsetting'): 'إعدادات النظام',
    ('core', 'purchase'): 'المشتريات',
    ('core', 'pagesetting'): 'تسميات الصفحات',
    ('core', 'productionbatch'): 'دفعات التحضير',
    ('core', 'inventoryitem'): 'مواد المخزون',
    ('core', 'productrecipeitem'): 'وصفات المنتجات',
    ('events', 'eventtickettype'): 'أنواع تذاكر الفعاليات',
    ('events', 'event'): 'الفعاليات',
    ('events', 'eventmedia'): 'صور الفعاليات',
    ('internet', 'wifinetwork'): 'شبكات الإنترنت',
    ('members', 'membercreditledger'): 'سجل رصيد الأعضاء',
    ('members', 'membershipbenefitrule'): 'قواعد مزايا العضوية',
    ('members', 'membershipplan'): 'خطط العضوية',
    ('members', 'membershipsubscription'): 'اشتراكات العضوية',
    ('reservations', 'reservation'): 'الحجوزات',
    ('vendors', 'vendorparticipation'): 'مشاركات البائعين',
    ('vendors', 'vendor'): 'البائعون والشركاء',
    ('vendors', 'vendormedia'): 'صور البائعين',
}

DASHBOARD_SECTIONS = [
    ('المنتجات والمنيو', [('core','product'), ('catalog','menusection'), ('catalog','mediaasset'), ('catalog','productoption'), ('catalog','productoptiongroup'), ('catalog','prepstation')]),
    ('التشغيل والمبيعات', [('core','order'), ('core','payment'), ('core','shift'), ('core','room'), ('core','tablearea'), ('core','dailyclose')]),
    ('المالية والمخزون', [('core','expense'), ('core','expensecategory'), ('core','cashmovement'), ('core','purchase'), ('core','inventoryitem'), ('core','productrecipeitem'), ('core','productionbatch')]),
    ('الأعضاء والإنترنت', [('core','member'), ('members','membershipplan'), ('members','membershipsubscription'), ('members','membershipbenefitrule'), ('core','internetpackage'), ('core','internetsession'), ('internet','wifinetwork')]),
    ('الفعاليات والحجوزات', [('events','event'), ('events','eventtickettype'), ('reservations','reservation'), ('vendors','vendor'), ('vendors','vendorparticipation')]),
    ('الإعدادات', [('core','systemsetting'), ('core','pagesetting'), ('accounts','user'), ('auth','group'), ('core','notificationpreference')]),
]

STAFF_LINKS = [
    ('لوحة التشغيل', '/staff/'), ('نقطة البيع', '/staff/pos/'), ('الطلبات', '/staff/orders/'), ('الكاشير', '/staff/cashier/'),
    ('التحضير', '/staff/prep/'), ('التقارير', '/staff/reports/'), ('إغلاق اليوم', '/staff/close-day/'), ('أدوات المنيو', '/staff/menu-tools/'),
    ('روابط QR', '/staff/qr/'), ('المالية', '/staff/finance/'), ('المخزون', '/staff/inventory/'),
]
QUICK_ACTIONS = [
    ('إضافة منتج', 'admin:core_product_add', None), ('رفع وسائط', 'admin:catalog_mediaasset_add', None),
    ('إدارة توفر المنتجات', 'admin:catalog_productavailability_changelist', None), ('فتح نقطة البيع', None, '/staff/pos/'),
    ('فتح الطلبات', None, '/staff/orders/'), ('فتح الكاشير', None, '/staff/cashier/'), ('فتح التقارير', None, '/staff/reports/'),
    ('فتح إغلاق اليوم', None, '/staff/close-day/'), ('فتح أدوات المنيو', None, '/staff/menu-tools/'),
]

def label_for(app_label, model_name, fallback):
    return MODEL_LABELS_AR.get((app_label, model_name), fallback)

def admin_url(name):
    try:
        return reverse(name)
    except NoReverseMatch:
        return '#'

def build_dashboard(site, request):
    registry = {(m._meta.app_label, m._meta.model_name): (m, a) for m, a in site._registry.items()}
    sections = []
    for title, keys in DASHBOARD_SECTIONS:
        items = []
        for key in keys:
            if key not in registry:
                continue
            model, model_admin = registry[key]
            perms = model_admin.get_model_perms(request)
            if not any(perms.values()):
                continue
            app_label, model_name = key
            info = (app_label, model_name)
            items.append({
                'label': label_for(app_label, model_name, model._meta.verbose_name_plural),
                'url': admin_url('admin:%s_%s_changelist' % info),
                'add_url': admin_url('admin:%s_%s_add' % info) if perms.get('add') else None,
            })
        sections.append({'title': title, 'items': items})
    return sections

def install_hub_admin(site=admin.site):
    site.site_header = 'إدارة Hub / Masharib'
    site.site_title = 'إدارة Hub'
    site.index_title = 'لوحة إدارة مشاريب'
    site.index_template = 'admin/index.html'
    site.app_index_template = 'admin/app_index.html'
    site.enable_nav_sidebar = True
    original_get_app_list = site.get_app_list
    original_each_context = site.each_context
    def get_app_list(request, app_label=None):
        apps = original_get_app_list(request, app_label)
        for app in apps:
            app['name'] = APP_LABELS_AR.get(app['app_label'], app['name'])
            filtered = []
            for model in app['models']:
                key = (app['app_label'], model['object_name'].lower())
                model['name'] = label_for(*key, fallback=model['name'])
                if key not in HIDDEN_ADMIN_MODELS:
                    filtered.append(model)
            app['models'] = filtered
        return [app for app in apps if app['models']]
    def each_context(request):
        context = original_each_context(request)
        context.update({
            'hub_dashboard_sections': build_dashboard(site, request),
            'hub_staff_links': [{'label': l, 'url': u} for l, u in STAFF_LINKS],
            'hub_quick_actions': [{'label': l, 'url': admin_url(n) if n else u} for l, n, u in QUICK_ACTIONS],
            'hub_hidden_models': sorted(MODEL_LABELS_AR.get(k, k[1]) for k in HIDDEN_ADMIN_MODELS),
        })
        return context
    site.get_app_list = get_app_list
    site.each_context = each_context
