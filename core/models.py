import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


def _arabic_first(obj, *fields, fallback='—'):
    for field in fields:
        value = getattr(obj, field, '')
        if value:
            return str(value)
    return fallback


def validate_font_upload(file_obj):
    ext = Path(file_obj.name).suffix.lower()
    if ext not in {'.woff', '.woff2', '.ttf', '.otf'}:
        raise ValidationError('صيغة الخط غير مسموحة. الصيغ المسموحة: woff, woff2, ttf, otf.')
    if file_obj.size and file_obj.size > 2 * 1024 * 1024:
        raise ValidationError('حجم ملف الخط يجب ألا يتجاوز 2MB.')


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class PublicCodeModel(models.Model):
    public_code = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    class Meta:
        abstract = True


class Room(TimeStampedModel, PublicCodeModel):
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)

    def __str__(self):
        return _arabic_first(self, 'name_ar', 'name_en', fallback=str(self.public_code)[:8])


class TableArea(TimeStampedModel, PublicCodeModel):
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='tables')
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    qr_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    def __str__(self):
        table_name = _arabic_first(self, 'name_ar', 'name_en', fallback=str(self.qr_token)[:8])
        room_name = str(self.room) if self.room_id else ''
        return f'{table_name} — {room_name}' if room_name else table_name


class Category(TimeStampedModel, PublicCodeModel):
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    description_ar = models.TextField(blank=True)
    description_en = models.TextField(blank=True)

    def __str__(self):
        return _arabic_first(self, 'name_ar', 'name_en', fallback=str(self.public_code)[:8])


class Product(TimeStampedModel, PublicCodeModel):
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='products')
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    description_ar = models.TextField(blank=True)
    description_en = models.TextField(blank=True)
    price_syp = models.PositiveIntegerField()
    is_available = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    menu_sections = models.ManyToManyField('catalog.MenuSection', blank=True, related_name='products')
    tags = models.ManyToManyField('catalog.Tag', blank=True, related_name='products')

    class ItemType(models.TextChoices):
        BEVERAGE = 'beverage', 'Beverage'
        FOOD = 'food', 'Food'
        SERVICE = 'service', 'Service'
        EVENT_TICKET = 'event_ticket', 'Event Ticket'
        RESERVATION = 'reservation', 'Reservation'
        MEMBERSHIP = 'membership', 'Membership'
        RETAIL = 'retail', 'Retail'
        ADDON = 'addon', 'Addon'

    class BeverageType(models.TextChoices):
        COFFEE='coffee','Coffee'; TEA='tea','Tea'; MATE='mate','Mate'; JUICE='juice','Juice'; ZHOURAT='zhourat','Zhourat'; LOCAL='local','Local'; ARAK='arak','Arak'; WINE='wine','Wine'; BEER='beer','Beer'; COCKTAIL='cocktail','Cocktail'; OTHER='other','Other'

    class FoodType(models.TextChoices):
        HUB_FOOD='hub_food','Hub Food'; VENDOR_FOOD='vendor_food','Vendor Food'; GUEST_CHEF='guest_chef','Guest Chef'; SNACK='snack','Snack'; DAILY_DISH='daily_dish','Daily Dish'; OTHER='other','Other'

    class ServiceType(models.TextChoices):
        INTERNET='internet','Internet'; WORKSPACE='workspace','Workspace'; TABLE_RESERVATION='table_reservation','Table Reservation'; ROOM_BOOKING='room_booking','Room Booking'; WORKSHOP='workshop','Workshop'; OTHER='other','Other'

    item_type = models.CharField(max_length=30, choices=ItemType.choices, default=ItemType.BEVERAGE)
    beverage_type = models.CharField(max_length=30, choices=BeverageType.choices, blank=True)
    food_type = models.CharField(max_length=30, choices=FoodType.choices, blank=True)
    service_type = models.CharField(max_length=30, choices=ServiceType.choices, blank=True)
    prep_station_ref = models.ForeignKey('catalog.PrepStation', on_delete=models.SET_NULL, null=True, blank=True)
    vendor = models.ForeignKey('vendors.Vendor', on_delete=models.SET_NULL, null=True, blank=True)
    is_alcoholic = models.BooleanField(default=False)
    age_restricted = models.BooleanField(default=False)
    visible_on_qr = models.BooleanField(default=True)
    orderable_on_qr = models.BooleanField(default=True)
    requires_staff_confirmation = models.BooleanField(default=False)
    available_for_events = models.BooleanField(default=True)
    available_for_takeaway = models.BooleanField(default=False)
    not_discountable = models.BooleanField(default=False)
    cost_syp = models.PositiveIntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return _arabic_first(self, 'name_ar', 'name_en', fallback=str(self.public_code)[:8])


class Order(TimeStampedModel, PublicCodeModel):
    class ServiceMode(models.TextChoices):
        DINE_IN = 'dine_in', 'طلب عام داخل المكان'
        TABLE = 'table', 'طلب طاولة'
        TAKEAWAY = 'takeaway', 'تيك أواي'

    class Status(models.TextChoices):
        NEW = 'new', 'جديد'
        ACCEPTED = 'accepted', 'مقبول'
        PREPARING = 'preparing', 'قيد التحضير'
        READY = 'ready', 'جاهز'
        SERVED = 'served', 'تم التقديم'
        CANCELLED = 'cancelled', 'ملغى'

    table = models.ForeignKey(TableArea, on_delete=models.PROTECT, related_name='orders', null=True, blank=True)
    service_mode = models.CharField(max_length=20, choices=ServiceMode.choices, default=ServiceMode.DINE_IN)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    notes = models.TextField(blank=True)

    @property
    def display_number(self):
        if self.pk:
            return f'#{self.pk:05d}'
        return f'#{str(self.public_code)[:8].upper()}'

    @property
    def location_label(self):
        if self.table_id:
            return f'الطاولة: {self.table.name_ar}'
        if self.service_mode == self.ServiceMode.TAKEAWAY:
            return 'تيك أواي'
        return 'طلب عام داخل المكان'

    @property
    def location_detail(self):
        if self.table_id and self.table.room_id:
            return f'المساحة: {self.table.room.name_ar}'
        return ''

    def __str__(self):
        return self.display_number


class OrderItem(TimeStampedModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='order_items')
    quantity = models.PositiveIntegerField(default=1)
    product_name_ar_snapshot = models.CharField(max_length=120)
    product_name_en_snapshot = models.CharField(max_length=120, blank=True)
    unit_price_syp_snapshot = models.PositiveIntegerField()
    selected_options_snapshot = models.JSONField(default=list, blank=True)
    item_note = models.TextField(blank=True)
    line_total_syp_snapshot = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return f'{self.product_name_ar_snapshot} × {self.quantity} — {self.order.display_number}'


class Payment(TimeStampedModel, PublicCodeModel):
    class Method(models.TextChoices):
        CASH = 'cash', 'نقداً'
        MANUAL_TRANSFER = 'manual_transfer', 'تحويل يدوي'
        UNPAID = 'unpaid', 'غير مدفوع'
        FREE = 'free', 'ضيافة'
        MEMBER_DISCOUNT = 'member_discount', 'خصم عضو'

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='payments')
    amount_syp = models.PositiveIntegerField()
    method = models.CharField(max_length=30, choices=Method.choices, default=Method.UNPAID)

    def __str__(self):
        return f'{self.order.display_number} — {self.get_method_display()} — {self.amount_syp} ل.س'


class Member(TimeStampedModel, PublicCodeModel):
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=30, unique=True)
    balance_syp = models.IntegerField(default=0)
    default_plan = models.ForeignKey('members.MembershipPlan', on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        name = _arabic_first(self, 'name_ar', 'name_en', fallback=self.phone)
        return f'{name} — {self.phone}' if self.phone else name


class InternetPackage(TimeStampedModel, PublicCodeModel):
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    description_ar = models.TextField(blank=True)
    description_en = models.TextField(blank=True)
    duration_minutes = models.PositiveIntegerField()
    price_syp = models.PositiveIntegerField()

    def __str__(self):
        return _arabic_first(self, 'name_ar', 'name_en', fallback=str(self.public_code)[:8])


class InternetSession(TimeStampedModel, PublicCodeModel):
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name='internet_sessions', null=True, blank=True)
    package = models.ForeignKey(InternetPackage, on_delete=models.PROTECT, related_name='sessions')
    customer_name = models.CharField(max_length=120, blank=True)
    customer_phone = models.CharField(max_length=30, blank=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    actual_duration_minutes = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, default='active')
    notes = models.TextField(blank=True)
    consumed = models.BooleanField(default=False)

    def __str__(self):
        customer = self.member or self.customer_name or self.customer_phone or 'زائر'
        return f'{customer} — {self.package} — {self.start_time:%Y-%m-%d %H:%M}'


class Shift(TimeStampedModel, PublicCodeModel):
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='opened_shifts')
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='closed_shifts', null=True, blank=True)
    opened_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'وردية {self.opened_at:%Y-%m-%d %H:%M} — {self.opened_by}'


class ActivityLog(TimeStampedModel):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=120)
    details = models.JSONField(default=dict, blank=True)

    def __str__(self):
        actor = self.actor or 'النظام'
        stamp = self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else ''
        return f'{stamp} — {actor} — {self.action}'


class SystemSetting(TimeStampedModel):
    class Language(models.TextChoices):
        ARABIC = 'ar', 'العربية'
        ENGLISH = 'en', 'English'

    class DefaultOrderMode(models.TextChoices):
        DINE_IN = 'dine_in', 'طلب عام داخل المكان'
        TABLE = 'table', 'طلب طاولة'
        TAKEAWAY = 'takeaway', 'تيك أواي'

    system_title_ar = models.CharField(max_length=160, default='نظام مشاريب')
    system_title_en = models.CharField(max_length=160, default='Masharib System')
    public_brand_title_ar = models.CharField(max_length=160, default='مشاريب')
    public_brand_title_en = models.CharField(max_length=160, default='Masharib')
    header_subtitle_ar = models.CharField(max_length=220, default='Hub Sueda • تشغيل يومي مرن')
    header_subtitle_en = models.CharField(max_length=220, default='Hub Sueda • flexible daily operations')
    default_language = models.CharField(max_length=5, choices=Language.choices, default=Language.ARABIC)
    enable_takeaway = models.BooleanField(default=False)
    enable_table_orders = models.BooleanField(default=True)
    enable_general_in_space_orders = models.BooleanField(default=True)
    show_internal_order_uuid = models.BooleanField(default=False)
    default_order_mode = models.CharField(max_length=20, choices=DefaultOrderMode.choices, default=DefaultOrderMode.DINE_IN)
    primary_color = models.CharField(max_length=20, default='#0f5f57')
    header_color = models.CharField(max_length=20, default='#0f5f57')
    button_color = models.CharField(max_length=20, default='#0f5f57')
    accent_color = models.CharField(max_length=20, default='#c88a2b')
    background_color = models.CharField(max_length=20, default='#f6f1e8')
    card_background_color = models.CharField(max_length=20, default='#fffaf1')
    text_color = models.CharField(max_length=20, default='#262626')
    border_color = models.CharField(max_length=20, default='#ddd2c0')
    base_font_size_px = models.PositiveIntegerField(default=18)
    heading_font_size_px = models.PositiveIntegerField(null=True, blank=True)
    border_radius_px = models.PositiveIntegerField(default=18)
    custom_font_name = models.CharField(max_length=120, blank=True)
    custom_font_file = models.FileField(upload_to='system/fonts/', blank=True, validators=[validate_font_upload])

    class Meta:
        verbose_name = 'إعدادات النظام'
        verbose_name_plural = 'إعدادات النظام'

    def __str__(self):
        return self.system_title_ar or self.system_title_en

    def save(self, *args, **kwargs):
        if not self.pk and SystemSetting.objects.exists():
            self.pk = SystemSetting.objects.order_by('-updated_at', '-pk').first().pk
        super().save(*args, **kwargs)

    @property
    def custom_font_format(self):
        ext = Path(self.custom_font_file.name).suffix.lower().lstrip('.') if self.custom_font_file else ''
        return 'truetype' if ext == 'ttf' else ('opentype' if ext == 'otf' else ext or 'woff2')

    @property
    def system_title(self):
        return self.system_title_ar if self.default_language == self.Language.ARABIC else (self.system_title_en or self.system_title_ar)

    @property
    def public_brand_title(self):
        return self.public_brand_title_ar if self.default_language == self.Language.ARABIC else (self.public_brand_title_en or self.public_brand_title_ar)

    @property
    def header_subtitle(self):
        return self.header_subtitle_ar if self.default_language == self.Language.ARABIC else (self.header_subtitle_en or self.header_subtitle_ar)


class PageSetting(TimeStampedModel):
    key = models.SlugField(max_length=80, unique=True)
    title_ar = models.CharField(max_length=160)
    title_en = models.CharField(max_length=160, blank=True)
    subtitle_ar = models.CharField(max_length=240, blank=True)
    subtitle_en = models.CharField(max_length=240, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'تسمية صفحة'
        verbose_name_plural = 'تسميات الصفحات'

    def __str__(self):
        return self.title_ar or self.title_en or self.key

    def title_for_language(self, language='ar', fallback=''):
        if language == 'en':
            return self.title_en or self.title_ar or fallback
        return self.title_ar or self.title_en or fallback

    def subtitle_for_language(self, language='ar', fallback=''):
        if language == 'en':
            return self.subtitle_en or self.subtitle_ar or fallback
        return self.subtitle_ar or self.subtitle_en or fallback
