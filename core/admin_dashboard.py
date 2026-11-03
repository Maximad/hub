from django.contrib import admin
from django.urls import NoReverseMatch, reverse
from accounts.permissions import user_has_capability

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
    ('الطلبات والخدمة', [('core','order'), ('core','payment'), ('core','hubvisit'), ('core','shift'), ('core','room'), ('core','tablearea')]),
    ('المنتجات والمنيو والوسائط', [('core','product'), ('catalog','menusection'), ('catalog','mediaasset'), ('catalog','productoption'), ('catalog','productoptiongroup'), ('catalog','prepstation'), ('core','category')]),
    ('المخزون والمشتريات', [('core','inventoryitem'), ('core','purchase'), ('core','productrecipeitem'), ('core','productionbatch')]),
    ('المالية والتسويات', [('core','expense'), ('core','expensecategory'), ('core','cashmovement'), ('core','dailyclose'), ('core','exchangerate')]),
    ('العملاء والعضويات والإنترنت', [('core','member'), ('members','membershipplan'), ('members','membershipsubscription'), ('members','membershipbenefitrule'), ('core','internetpackage'), ('core','internetsession'), ('core','internetentitlement'), ('internet','wifinetwork')]),
    ('الفعاليات والحجوزات والشركاء', [('events','event'), ('reservations','reservation'), ('members','program'), ('vendors','vendor'), ('vendors','vendorparticipation'), ('events','eventtickettype')]),
    ('المستخدمون والإعدادات والتشخيص', [('accounts','user'), ('auth','group'), ('core','systemsetting'), ('core','pagesetting'), ('core','homepagefeature'), ('core','notificationpreference')]),
]

SECTION_DESCRIPTIONS = {
    'الطلبات والخدمة': 'ابحث عن الطلب أو الحساب أو الدفعة؛ استخدم شاشة التشغيل للبيع والقبض.',
    'المنتجات والمنيو والوسائط': 'الأسعار، الظهور في المنيو، الصور، التصنيفات وخيارات المنتجات.',
    'المخزون والمشتريات': 'المواد والوصفات وطلبات الشراء والتحضير؛ الاستلام يتم عبر شاشته المعتمدة.',
    'المالية والتسويات': 'المصروفات والصندوق والإغلاق وأسعار الصرف؛ احتفظ بمسارات القيد الآمنة.',
    'العملاء والعضويات والإنترنت': 'العملاء والاشتراكات والباقات والجلسات المرتبطة بهم.',
    'الفعاليات والحجوزات والشركاء': 'الفعاليات والحضور والحجوزات والشراكات الثقافية.',
    'المستخدمون والإعدادات والتشخيص': 'الصلاحيات وإعدادات النظام والمحتوى المنشور والأدوات المتقدمة.',
}
MODEL_SEARCH_TERMS = {
    ('core','order'): 'orders sales bill invoice table طلب فاتورة مبيعات',
    ('core','payment'): 'payment receipt collection cashier قبض إيصال تسوية',
    ('core','hubvisit'): 'visit account bill tab جلسة حساب طاولة',
    ('core','purchase'): 'purchase supplier receipt procurement مشتريات توريد استلام مورد فاتورة',
    ('core','inventoryitem'): 'inventory stock material مخزون مادة جرد',
    ('core','productrecipeitem'): 'recipe ingredient cost وصفة مكونات تكلفة',
    ('core','product'): 'product menu price item صنف منيو سعر',
    ('catalog','mediaasset'): 'media image photo upload صور وسائط رفع',
    ('core','expense'): 'expense spending payment cost مصروف نفقة',
    ('core','dailyclose'): 'closing reconciliation close day إغلاق تسوية يوم',
    ('core','exchangerate'): 'exchange rate currency dollar usd سعر صرف دولار',
    ('core','member'): 'customer membership client عضو زبون عميل',
    ('members','membershipplan'): 'membership subscription plan عضوية اشتراك خطة',
    ('core','internetsession'): 'internet wifi hotspot session شبكة انترنت واي فاي',
    ('events','event'): 'events calendar concert حفلة حفل فعالية موعد',
    ('reservations','reservation'): 'reservation booking table حجز حضور طاولة',
    ('vendors','vendor'): 'partner supplier vendor شريك بائع مورد',
    ('accounts','user'): 'staff people permissions login employee مستخدم موظف صلاحيات',
    ('core','systemsetting'): 'settings preferences configuration setup إعدادات ضبط',
    ('core','homepagefeature'): 'home homepage featured publication editorial واجهة رئيسية نشر',
}
SECTION_WORKFLOWS = {
    'الطلبات والخدمة': [('نقطة البيع', '/staff/pos/', 'بيع وطلب جديد · POS'), ('الكاشير', '/staff/cashier/', 'تحصيل ومتابعة الحسابات'), ('التحضير', '/staff/kitchen/', 'الطلبات المرسلة للمطبخ والبار')],
    'المنتجات والمنيو والوسائط': [('إدارة المنيو', '/staff/menu-tools/', 'تجهيز القائمة والظهور'), ('المنيو العام', '/menu/', 'معاينة ما يراه الزائر')],
    'المخزون والمشتريات': [('المخزون', '/staff/inventory/', 'الجرد والمشتريات والوصفات')],
    'المالية والتسويات': [('المالية', '/staff/finance/', 'التسويات والمصروفات'), ('إغلاق اليوم', '/staff/close-day/', 'تسوية يوم التشغيل')],
    'العملاء والعضويات والإنترنت': [('الإنترنت', '/staff/internet/', 'الجلسات والعمل'), ('الأعضاء', '/staff/members/', 'الملفات والعضويات')],
    'الفعاليات والحجوزات والشركاء': [('الفعاليات', '/staff/events/', 'البرنامج التشغيلي'), ('الحجوزات', '/staff/reservations/', 'استقبال ومتابعة الحضور')],
    'المستخدمون والإعدادات والتشخيص': [('المستخدمون', '/staff/users/', 'حسابات الموظفين'), ('التقارير', '/staff/reports/', 'المتابعة والتشخيص')],
}

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

# Navigation is advisory; existing route decorators remain the authority.
STAFF_WORKFLOW_CAPABILITIES = {
    '/staff/pos/': 'pos', '/staff/orders/': 'orders',
    '/staff/cashier/': 'cashier', '/staff/kitchen/': 'kitchen_board',
    '/staff/prep/': 'kitchen_board', '/staff/menu-tools/': 'settings',
    '/staff/inventory/': 'inventory', '/staff/finance/': 'finance',
    '/staff/close-day/': 'reports', '/staff/internet/': 'members/internet',
    '/staff/members/': 'members/internet', '/staff/events/': 'events',
    '/staff/reservations/': 'reservations', '/staff/users/': 'users',
    '/staff/reports/': 'reports', '/staff/qr/': 'settings',
}
PUBLIC_ADMIN_SHORTCUTS = {'/menu/'}


def can_show_workflow(request, url):
    if url in PUBLIC_ADMIN_SHORTCUTS:
        return True
    capability = STAFF_WORKFLOW_CAPABILITIES.get(url)
    return bool(capability and user_has_capability(request.user, capability))


def quick_actions_for(site, request):
    visible = []
    for label, admin_name, url in QUICK_ACTIONS:
        if admin_name:
            try:
                model_info = admin_name.removeprefix('admin:').rsplit('_', 1)[0].split('_', 1)
                app_label, model_name = model_info
                model_admin = next((a for model, a in site._registry.items()
                                    if model._meta.app_label == app_label and model._meta.model_name == model_name), None)
                if model_admin is None:
                    continue
                perms = model_admin.get_model_perms(request)
                needed = 'add' if admin_name.endswith('_add') else 'view'
                if not (perms.get(needed) or (needed == 'view' and perms.get('change'))):
                    continue
            except ValueError:
                continue
            target = admin_url(admin_name)
            if target != '#':
                visible.append({'label': label, 'url': target})
        elif url and can_show_workflow(request, url):
            visible.append({'label': label, 'url': url})
    return visible


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
            list_url = admin_url('admin:%s_%s_changelist' % key)
            if list_url == '#':
                continue
            label = label_for(app_label, model_name, model._meta.verbose_name_plural)
            search_text = ' '.join((
                str(label), str(model._meta.verbose_name_plural), model.__name__,
                app_label, model_name, MODEL_SEARCH_TERMS.get(key, ''), title,
            ))
            add_url = admin_url('admin:%s_%s_add' % key) if perms.get('add') else None
            items.append({
                'label': label, 'url': list_url, 'add_url': add_url if add_url != '#' else None,
                'search_text': search_text,
            })
        workflows = [{'label': label, 'url': url, 'description': hint,
                      'search_text': ' '.join((label, hint, title))}
                     for label, url, hint in SECTION_WORKFLOWS.get(title, ()) if can_show_workflow(request, url)]
        if items or workflows:
            sections.append({'title': title, 'description': SECTION_DESCRIPTIONS[title],
                             'items': items, 'workflows': workflows})
    return sections


def install_hub_admin(site=admin.site):
    site.site_header = 'إدارة Hub / Masharib'
    site.site_title = 'إدارة Hub'
    site.index_title = 'دليل إدارة هَبّ السويداء'
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
            'hub_staff_links': [{'label': l, 'url': u} for l, u in STAFF_LINKS if can_show_workflow(request, u)],
            'hub_quick_actions': quick_actions_for(site, request),
            'hub_hidden_models': sorted(MODEL_LABELS_AR.get(k, k[1]) for k in HIDDEN_ADMIN_MODELS),
        })
        return context
    site.get_app_list = get_app_list
    site.each_context = each_context
