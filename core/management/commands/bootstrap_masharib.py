from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import Room, TableArea, Product, Category, InternetPackage, Member
from catalog.models import MenuSection, ProductOption, ProductOptionGroup, ProductOptionGroupAssignment, Tag, PrepStation
from members.models import MembershipPlan, MembershipBenefitRule, MembershipSubscription
from internet.models import WifiNetwork
from accounts.models import User


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument('--default-password', dest='default_password', default=None)

    def handle(self, *args, **opts):
        summary = {}

        drinks, _ = MenuSection.objects.update_or_create(name_ar='مشروبات', defaults={'sort_order': 1})
        coffee, _ = MenuSection.objects.update_or_create(name_ar='قهوة وشاي', defaults={'parent': drinks})
        mate, _ = MenuSection.objects.update_or_create(name_ar='متّة', defaults={'parent': drinks})
        local, _ = MenuSection.objects.update_or_create(name_ar='مشروبات محلية', defaults={'parent': drinks})
        zhourat, _ = MenuSection.objects.update_or_create(name_ar='زهورات', defaults={'parent': drinks})
        arak, _ = MenuSection.objects.update_or_create(name_ar='عرق', defaults={'parent': drinks})
        wine, _ = MenuSection.objects.update_or_create(name_ar='نبيذ', defaults={'parent': drinks})
        beer, _ = MenuSection.objects.update_or_create(name_ar='بيرة', defaults={'parent': drinks})
        cocktails, _ = MenuSection.objects.update_or_create(name_ar='كوكتيلات', defaults={'parent': drinks})
        food, _ = MenuSection.objects.update_or_create(name_ar='أكل', defaults={})
        snack, _ = MenuSection.objects.update_or_create(name_ar='سناك', defaults={'parent': food})
        hub_food, _ = MenuSection.objects.update_or_create(name_ar='مطبخ مشاريب', defaults={'parent': food})
        guest, _ = MenuSection.objects.update_or_create(name_ar='ضيف / Guest Chef', defaults={'parent': food})
        internet_section, _ = MenuSection.objects.update_or_create(name_ar='إنترنت ومساحة عمل', defaults={})
        reservations_section, _ = MenuSection.objects.update_or_create(name_ar='حجوزات', defaults={})
        events_section, _ = MenuSection.objects.update_or_create(name_ar='فعاليات', defaults={})
        addons_section, _ = MenuSection.objects.update_or_create(name_ar='إضافات', defaults={})

        tags = {}
        for code in ['alcoholic','seasonal','breakfast','guest-chef','ramadan','blues-night','members-only','not-discountable','local','food-lab','workspace','internet']:
            tags[code], _ = Tag.objects.update_or_create(code=code, defaults={'name_ar': code, 'tag_type': 'operational'})

        stations = {}
        for code, name in [('bar','بار مشاريب'),('kitchen','مطبخ مشاريب'),('vendor','Food Lab'),('cashier','الكاشير'),('internet','الإنترنت والعمل'),('event','الفعاليات'),('none','بدون تحضير')]:
            stations[code], _ = PrepStation.objects.update_or_create(code=code, defaults={'name_ar': name})

        room, _ = Room.objects.update_or_create(name_ar='مشاريب', defaults={})
        for t in ['T01 طاولة 1','T02 طاولة 2','T03 طاولة 3','T04 طاولة 4','BAR البار','GENERAL طلب عام داخل المكان','WORK01 مكتب 1','WORK02 مكتب 2','EVENT فعالية عامة']:
            TableArea.objects.update_or_create(room=room, name_ar=t)

        cat, _ = Category.objects.update_or_create(name_ar='عام', defaults={})
        def upsert_product(name_ar, price, section, **defaults):
            p, _ = Product.objects.update_or_create(name_ar=name_ar, defaults={'category': cat, 'price_syp': price, **defaults})
            p.menu_sections.set([section])
            return p

        upsert_product('قهوة عربية', 20000, coffee, item_type='beverage', beverage_type='coffee', prep_station_ref=stations['bar'])
        upsert_product('شاي', 15000, coffee, item_type='beverage', beverage_type='tea', prep_station_ref=stations['bar'])
        upsert_product('متّة', 25000, mate, item_type='beverage', beverage_type='mate', prep_station_ref=stations['bar'])
        p = upsert_product('عرق كأس', 35000, arak, item_type='beverage', beverage_type='arak', is_alcoholic=True, age_restricted=True, requires_staff_confirmation=True, prep_station_ref=stations['bar'])
        p.tags.set([tags['alcoholic']])
        upsert_product('طبق سناك', 35000, snack, item_type='food', food_type='snack', prep_station_ref=stations['kitchen'])
        upsert_product('ساعة إنترنت', 15000, internet_section, item_type='service', service_type='internet', prep_station_ref=stations['internet'])
        upsert_product('حجز طاولة', 0, reservations_section, item_type='reservation', prep_station_ref=stations['cashier'])
        upsert_product('تذكرة فعالية', 50000, events_section, item_type='event_ticket', prep_station_ref=stations['event'])
        upsert_product('ثلج إضافي', 5000, addons_section, item_type='addon', prep_station_ref=stations['bar'])


        option_groups = {}
        option_defs = [
            ('sugar', 'السكر', 'single', 'beverage', [('no_sugar', 'بدون سكر'), ('light_sugar', 'سكر خفيف'), ('normal_sugar', 'عادي'), ('extra_sugar', 'زيادة سكر')]),
            ('temperature', 'الحرارة', 'single', 'beverage', [('hot', 'ساخن'), ('cold', 'بارد')]),
            ('drink_additions', 'إضافات المشروبات', 'multiple', 'beverage', [('lemon', 'ليمون'), ('mint', 'نعنع'), ('milk', 'حليب')]),
            ('ice', 'الثلج', 'single', 'beverage', [('no_ice', 'بدون ثلج'), ('normal_ice', 'ثلج عادي'), ('extra_ice', 'ثلج إضافي')]),
            ('remove_ingredients', 'إزالة مكونات', 'multiple', 'food', [('no_onion', 'بدون بصل'), ('no_garlic', 'بدون ثوم'), ('no_sauce', 'بدون صوص')]),
            ('food_additions', 'إضافات الأكل', 'multiple', 'food', [('extra_cheese', 'زيادة جبنة'), ('extra_sauce', 'زيادة صوص'), ('extra_bread', 'زيادة خبز')]),
            ('spice_level', 'درجة الحر', 'single', 'food', [('mild', 'خفيف'), ('medium', 'وسط'), ('hot', 'حر')]),
        ]
        for group_order, (code, name_ar, selection_type, item_type, options) in enumerate(option_defs, start=1):
            group, _ = ProductOptionGroup.objects.update_or_create(
                code=code,
                defaults={
                    'name_ar': name_ar,
                    'selection_type': selection_type,
                    'sort_order': group_order,
                    'is_active': True,
                    'applies_to_item_type': item_type,
                    'applies_to_beverage_type': '',
                    'applies_to_food_type': '',
                    'applies_to_service_type': '',
                },
            )
            option_groups[code] = group
            for option_order, (option_code, option_name_ar) in enumerate(options, start=1):
                ProductOption.objects.update_or_create(
                    group=group,
                    code=option_code,
                    defaults={'name_ar': option_name_ar, 'sort_order': option_order, 'is_active': True},
                )

        ProductOptionGroup.objects.filter(code='additions').update(
            name_ar='إضافات المشروبات',
            applies_to_item_type='beverage',
            applies_to_beverage_type='',
            applies_to_food_type='',
            applies_to_service_type='',
        )

        group_codes_by_item_type = {
            'beverage': ['sugar', 'temperature', 'drink_additions', 'ice'],
            'food': ['remove_ingredients', 'food_additions', 'spice_level'],
        }
        assignable_products = Product.objects.filter(item_type__in=group_codes_by_item_type).order_by('name_ar')
        for demo_product in assignable_products:
            group_codes = group_codes_by_item_type.get(demo_product.item_type, [])
            for assignment_order, group_code in enumerate(group_codes, start=1):
                ProductOptionGroupAssignment.objects.update_or_create(
                    product=demo_product,
                    group=option_groups[group_code],
                    defaults={'sort_order': assignment_order, 'is_active': True},
                )

        for assignment in ProductOptionGroupAssignment.objects.select_related('product', 'group').filter(is_active=True):
            if not assignment.group.applies_to_product(assignment.product):
                assignment.is_active = False
                assignment.save(update_fields=['is_active', 'updated_at'])

        WifiNetwork.objects.update_or_create(ssid='Masharib', defaults={'name_ar':'شبكة مشاريب','password':'CHANGE_ME_WIFI_PASSWORD','visible_on_qr':True,'show_password_on_qr':False,'is_active':True})

        for n,m,p in [('ساعة واحدة',60,15000),('ساعتان',120,25000),('٣ ساعات',180,35000),('يوم عمل',480,75000)]:
            InternetPackage.objects.update_or_create(name_ar=n, defaults={'duration_minutes':m,'price_syp':p})

        plan_defs=[('founding_cup','عضو كأس مؤسس','one_time'),('founding_chair','عضو كرسي مؤسس','one_time'),('friend','صديق مشاريب','custom'),('internet_weekly','عضوية إنترنت أسبوعية','weekly'),('internet_monthly','عضوية إنترنت شهرية','monthly'),('workspace_monthly','عضوية مساحة عمل شهرية','monthly'),('staff','فريق','custom'),('partner','شريك','custom'),('vip','VIP','custom')]
        plans={}
        for code,name,period in plan_defs:
            plans[code], _ = MembershipPlan.objects.update_or_create(code=code, defaults={'name_ar':name,'billing_period':period})

        MembershipBenefitRule.objects.update_or_create(plan=plans['founding_cup'], item_type='beverage', defaults={'discount_percent':10})
        MembershipBenefitRule.objects.update_or_create(plan=plans['friend'], item_type='beverage', defaults={'discount_percent':5})

        for name, phone, plan_code in [('عضو كأس مؤسس','0999000001','founding_cup'),('عضو كرسي مؤسس','0999000002','founding_chair'),('صديق مشاريب','0999000003','friend')]:
            member, _ = Member.objects.update_or_create(phone=phone, defaults={'name_ar':name,'default_plan':plans[plan_code]})
            MembershipSubscription.objects.update_or_create(member=member, plan=plans[plan_code], defaults={'starts_at': timezone.now(), 'status':'active'})

        for username, role in [('cashier','cashier'),('staff','waiter'),('kitchen','kitchen')]:
            u, created = User.objects.get_or_create(username=username, defaults={'phone': f'09{username[:3]}00000', 'role': role})
            if created:
                if opts['default_password']:
                    u.set_password(opts['default_password'])
                else:
                    u.set_unusable_password()
                u.save()

        self.stdout.write(self.style.SUCCESS('Bootstrap complete (idempotent, non-destructive).'))
