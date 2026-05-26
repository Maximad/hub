from django.core.management.base import BaseCommand
from datetime import timedelta
from django.utils import timezone
from core.models import Room, TableArea, Category, Product, InternetPackage, Member, InternetSession


class Command(BaseCommand):
    help = 'Seed Arabic demo data for Masharib.'

    def handle(self, *args, **options):
        room, _ = Room.objects.get_or_create(name_ar='الصالة الرئيسية', defaults={'name_en': 'Main Hall'})
        for label in ['طاولة 1', 'طاولة 2', 'طاولة 3']:
            TableArea.objects.get_or_create(room=room, name_ar=label)

        cat_hot, _ = Category.objects.get_or_create(name_ar='مشروبات ساخنة')
        cat_cold, _ = Category.objects.get_or_create(name_ar='مشروبات باردة')

        Product.objects.get_or_create(category=cat_hot, name_ar='قهوة عربية', defaults={'price_syp': 18000})
        Product.objects.get_or_create(category=cat_hot, name_ar='شاي', defaults={'price_syp': 12000})
        Product.objects.get_or_create(category=cat_cold, name_ar='عصير برتقال', defaults={'price_syp': 22000})

        pkg1, _ = InternetPackage.objects.get_or_create(name_ar='باقة ساعة', defaults={'duration_minutes': 60, 'price_syp': 25000})
        InternetPackage.objects.get_or_create(name_ar='باقة ثلاث ساعات', defaults={'duration_minutes': 180, 'price_syp': 60000})

        member, _ = Member.objects.get_or_create(name_ar='عميل تجريبي', phone='0999999999', defaults={'balance_syp': 100000})
        now = timezone.now()
        InternetSession.objects.get_or_create(member=member, package=pkg1, starts_at=now, ends_at=now + timedelta(minutes=60))

        self.stdout.write(self.style.SUCCESS('تمت إضافة البيانات التجريبية بنجاح.'))
