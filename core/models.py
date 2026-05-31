import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models


def _arabic_first(obj, *fields, fallback='—'):
    for field in fields:
        value = getattr(obj, field, '')
        if value:
            return str(value)
    return fallback




validate_hex_color = RegexValidator(
    regex=r'^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$',
    message='أدخل لوناً بصيغة HEX مثل #0f5f57 أو #fff.',
)

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

    class ButtonPaddingScale(models.TextChoices):
        COMPACT = 'compact', 'مضغوط'
        NORMAL = 'normal', 'عادي'
        LARGE = 'large', 'كبير'

    class InputSize(models.TextChoices):
        COMPACT = 'compact', 'مضغوط'
        NORMAL = 'normal', 'عادي'
        LARGE = 'large', 'كبير'

    class CardShadowLevel(models.TextChoices):
        NONE = 'none', 'بدون ظل'
        SOFT = 'soft', 'ظل خفيف'
        MEDIUM = 'medium', 'ظل متوسط'

    class UIDensity(models.TextChoices):
        COMPACT = 'compact', 'مضغوط'
        COMFORTABLE = 'comfortable', 'مريح'
        LARGE_TOUCH = 'large_touch', 'لمس كبير'

    class MenuLayoutPreset(models.TextChoices):
        DEFAULT_MASHARIB = 'default_masharib', 'مشاريب الافتراضي'
        MINIMAL_FAST = 'minimal_fast', 'سريع وبسيط'
        VISUAL_CARDS = 'visual_cards', 'بطاقات بصرية'
        COMPACT_LIST = 'compact_list', 'قائمة مضغوطة'
        TABLET_GRID = 'tablet_grid', 'شبكة تابلت'

    class MenuMobileLayout(models.TextChoices):
        LIST = 'list', 'قائمة'
        ONE_COLUMN_CARDS = 'one_column_cards', 'بطاقات عمود واحد'
        LARGE_IMAGE_CARDS = 'large_image_cards', 'بطاقات صور كبيرة'

    class ProductCardStyle(models.TextChoices):
        MINIMAL = 'minimal', 'بسيط'
        IMAGE_FIRST = 'image_first', 'الصورة أولاً'
        TEXT_FIRST = 'text_first', 'النص أولاً'
        COMPACT = 'compact', 'مضغوط'
        STORY_CARD = 'story_card', 'بطاقة قصة'

    class StickyCartStyle(models.TextChoices):
        BOTTOM_BAR = 'bottom_bar', 'شريط سفلي'
        FLOATING_BUTTON = 'floating_button', 'زر عائم'
        SIDEBAR_ON_DESKTOP = 'sidebar_on_desktop', 'جانبياً على سطح المكتب'

    class CartReviewPosition(models.TextChoices):
        BOTTOM = 'bottom', 'أسفل الصفحة'
        SIDEBAR_DESKTOP = 'sidebar_desktop', 'شريط جانبي على سطح المكتب'
        MODAL_DRAWER = 'modal_drawer', 'درج/نافذة للمراجعة'

    class StaffHomeLayout(models.TextChoices):
        GROUPED_CARDS = 'grouped_cards', 'بطاقات مجمّعة'
        COMPACT_GRID = 'compact_grid', 'شبكة مضغوطة'
        LARGE_TOUCH = 'large_touch', 'لمس كبير'

    class POSLayoutPreset(models.TextChoices):
        FAST_TOUCH = 'fast_touch', 'لمس سريع'
        IMAGE_GRID = 'image_grid', 'شبكة صور'
        COMPACT_LIST = 'compact_list', 'قائمة مضغوطة'
        TABLET_SPLIT = 'tablet_split', 'تقسيم تابلت'

    class POSCartPosition(models.TextChoices):
        BOTTOM = 'bottom', 'أسفل'
        SIDE = 'side', 'جانب'
        DRAWER = 'drawer', 'درج'

    class OrderBoardDensity(models.TextChoices):
        COMPACT = 'compact', 'مضغوط'
        NORMAL = 'normal', 'عادي'
        LARGE_TOUCH = 'large_touch', 'لمس كبير'

    class CashierLayout(models.TextChoices):
        SINGLE_COLUMN = 'single_column', 'عمود واحد'
        SUMMARY_SIDEBAR = 'summary_sidebar', 'ملخص جانبي'
        LARGE_PAYMENT = 'large_payment', 'دفع كبير'

    system_title_ar = models.CharField(max_length=160, default='نظام مشاريب', verbose_name='عنوان النظام بالعربية')
    system_title_en = models.CharField(max_length=160, default='Masharib System', verbose_name='عنوان النظام بالإنجليزية')
    public_brand_title_ar = models.CharField(max_length=160, default='مشاريب', verbose_name='اسم العلامة في المنيو')
    public_brand_title_en = models.CharField(max_length=160, default='Masharib', verbose_name='اسم العلامة بالإنجليزية')
    header_subtitle_ar = models.CharField(max_length=220, default='Hub Sueda • تشغيل يومي مرن', verbose_name='وصف الهيدر بالعربية')
    header_subtitle_en = models.CharField(max_length=220, default='Hub Sueda • flexible daily operations', verbose_name='وصف الهيدر بالإنجليزية')
    default_language = models.CharField(max_length=5, choices=Language.choices, default=Language.ARABIC, verbose_name='اللغة الافتراضية')
    enable_takeaway = models.BooleanField(default=False, verbose_name='تفعيل التيك أواي')
    enable_table_orders = models.BooleanField(default=True, verbose_name='تفعيل طلبات الطاولات')
    enable_general_in_space_orders = models.BooleanField(default=True, verbose_name='تفعيل الطلب العام داخل المكان')
    show_internal_order_uuid = models.BooleanField(default=False, verbose_name='إظهار رقم UUID الداخلي')
    default_order_mode = models.CharField(max_length=20, choices=DefaultOrderMode.choices, default=DefaultOrderMode.DINE_IN, verbose_name='وضع الطلب الافتراضي')

    primary_color = models.CharField(max_length=20, default='#0f5f57', validators=[validate_hex_color], verbose_name='اللون الأساسي')
    header_color = models.CharField(max_length=20, default='#0f5f57', validators=[validate_hex_color], verbose_name='لون الهيدر')
    background_color = models.CharField(max_length=20, default='#f6f1e8', validators=[validate_hex_color], verbose_name='لون خلفية الصفحة')
    card_background_color = models.CharField(max_length=20, default='#fffaf1', validators=[validate_hex_color], verbose_name='لون خلفية البطاقات')
    text_color = models.CharField(max_length=20, default='#262626', validators=[validate_hex_color], verbose_name='لون النص')
    muted_text_color = models.CharField(max_length=20, default='#6b6b6b', validators=[validate_hex_color], verbose_name='لون النص الثانوي')
    border_color = models.CharField(max_length=20, default='#ddd2c0', validators=[validate_hex_color], verbose_name='لون الحدود')
    button_color = models.CharField(max_length=20, default='#0f5f57', validators=[validate_hex_color], verbose_name='لون الأزرار')
    accent_color = models.CharField(max_length=20, default='#c88a2b', validators=[validate_hex_color], verbose_name='لون التمييز')

    base_font_size_px = models.PositiveIntegerField(default=18, validators=[MinValueValidator(13), MaxValueValidator(22)], verbose_name='حجم الخط الأساسي')
    heading_font_size_px = models.PositiveIntegerField(default=34, validators=[MinValueValidator(18), MaxValueValidator(44)], verbose_name='حجم العناوين')
    small_font_size_px = models.PositiveIntegerField(default=15, validators=[MinValueValidator(11), MaxValueValidator(20)], verbose_name='حجم الخط الصغير')
    button_font_size_px = models.PositiveIntegerField(default=16, validators=[MinValueValidator(13), MaxValueValidator(24)], verbose_name='حجم خط الأزرار')
    button_padding_scale = models.CharField(max_length=20, choices=ButtonPaddingScale.choices, default=ButtonPaddingScale.NORMAL, verbose_name='حجم حشوة الأزرار')
    input_size = models.CharField(max_length=20, choices=InputSize.choices, default=InputSize.NORMAL, verbose_name='حجم حقول الإدخال')
    border_radius_px = models.PositiveIntegerField(default=18, validators=[MinValueValidator(0), MaxValueValidator(32)], verbose_name='استدارة الزوايا')
    card_shadow_level = models.CharField(max_length=20, choices=CardShadowLevel.choices, default=CardShadowLevel.SOFT, verbose_name='ظل البطاقات')
    page_max_width_px = models.PositiveIntegerField(default=1200, validators=[MinValueValidator(360), MaxValueValidator(1600)], verbose_name='أقصى عرض للصفحة')
    ui_density = models.CharField(max_length=20, choices=UIDensity.choices, default=UIDensity.COMFORTABLE, verbose_name='كثافة الواجهة')
    custom_font_name = models.CharField(max_length=120, blank=True, verbose_name='اسم الخط المخصص')
    custom_font_file = models.FileField(upload_to='system/fonts/', blank=True, validators=[validate_font_upload], verbose_name='ملف الخط المخصص')

    menu_layout_preset = models.CharField(max_length=30, choices=MenuLayoutPreset.choices, default=MenuLayoutPreset.DEFAULT_MASHARIB, verbose_name='نمط تخطيط المنيو')
    menu_mobile_layout = models.CharField(max_length=30, choices=MenuMobileLayout.choices, default=MenuMobileLayout.ONE_COLUMN_CARDS, verbose_name='تخطيط الجوال للمنيو')
    menu_tablet_columns = models.PositiveSmallIntegerField(default=2, choices=[(1, '1'), (2, '2'), (3, '3')], verbose_name='أعمدة المنيو على التابلت')
    menu_desktop_columns = models.PositiveSmallIntegerField(default=3, choices=[(1, '1'), (2, '2'), (3, '3'), (4, '4')], verbose_name='أعمدة المنيو على سطح المكتب')
    product_card_style = models.CharField(max_length=30, choices=ProductCardStyle.choices, default=ProductCardStyle.IMAGE_FIRST, verbose_name='شكل بطاقة المنتج')
    show_public_header = models.BooleanField(default=True, verbose_name='إظهار هيدر المنيو العام')
    show_brand_name = models.BooleanField(default=True, verbose_name='إظهار اسم العلامة')
    show_header_subtitle = models.BooleanField(default=True, verbose_name='إظهار وصف الهيدر')
    show_table_banner = models.BooleanField(default=True, verbose_name='إظهار شريط الطاولة/المكان')
    show_section_chips = models.BooleanField(default=True, verbose_name='إظهار شرائح الأقسام')
    show_product_images = models.BooleanField(default=True, verbose_name='إظهار صور المنتجات')
    show_product_descriptions = models.BooleanField(default=True, verbose_name='إظهار وصف المنتجات')
    show_product_prices = models.BooleanField(default=True, verbose_name='إظهار الأسعار')
    show_product_badges = models.BooleanField(default=True, verbose_name='إظهار شارات المنتجات')
    show_modifier_summary = models.BooleanField(default=True, verbose_name='إظهار ملخص الخيارات')
    collapse_modifiers_by_default = models.BooleanField(default=True, verbose_name='طي خيارات المنتجات افتراضياً')
    show_item_notes = models.BooleanField(default=True, verbose_name='إظهار ملاحظات العناصر')
    show_customer_name_field = models.BooleanField(default=True, verbose_name='إظهار حقل اسم الزبون')
    show_customer_phone_field = models.BooleanField(default=True, verbose_name='إظهار حقل هاتف الزبون')
    show_general_note_field = models.BooleanField(default=True, verbose_name='إظهار حقل الملاحظة العامة')
    show_sticky_cart = models.BooleanField(default=True, verbose_name='إظهار السلة اللاصقة')
    sticky_cart_style = models.CharField(max_length=30, choices=StickyCartStyle.choices, default=StickyCartStyle.BOTTOM_BAR, verbose_name='شكل السلة اللاصقة')
    cart_review_position = models.CharField(max_length=30, choices=CartReviewPosition.choices, default=CartReviewPosition.BOTTOM, verbose_name='مكان مراجعة السلة')

    staff_home_layout = models.CharField(max_length=30, choices=StaffHomeLayout.choices, default=StaffHomeLayout.GROUPED_CARDS, verbose_name='تخطيط صفحة الفريق')
    pos_layout_preset = models.CharField(max_length=30, choices=POSLayoutPreset.choices, default=POSLayoutPreset.FAST_TOUCH, verbose_name='نمط نقطة البيع')
    pos_mobile_columns = models.PositiveSmallIntegerField(default=2, choices=[(1, '1'), (2, '2')], verbose_name='أعمدة POS على الجوال')
    pos_tablet_columns = models.PositiveSmallIntegerField(default=3, choices=[(1, '1'), (2, '2'), (3, '3')], verbose_name='أعمدة POS على التابلت')
    pos_desktop_columns = models.PositiveSmallIntegerField(default=4, choices=[(1, '1'), (2, '2'), (3, '3'), (4, '4')], verbose_name='أعمدة POS على سطح المكتب')
    pos_show_product_images = models.BooleanField(default=True, verbose_name='إظهار صور المنتجات في POS')
    pos_show_prices = models.BooleanField(default=True, verbose_name='إظهار الأسعار في POS')
    pos_show_section_chips = models.BooleanField(default=True, verbose_name='إظهار شرائح الأقسام في POS')
    pos_enable_search_bar = models.BooleanField(default=True, verbose_name='تفعيل شريط البحث في POS')
    pos_cart_position = models.CharField(max_length=20, choices=POSCartPosition.choices, default=POSCartPosition.SIDE, verbose_name='مكان سلة POS')
    order_board_density = models.CharField(max_length=20, choices=OrderBoardDensity.choices, default=OrderBoardDensity.NORMAL, verbose_name='كثافة لوحة الطلبات')
    cashier_layout = models.CharField(max_length=30, choices=CashierLayout.choices, default=CashierLayout.SINGLE_COLUMN, verbose_name='تخطيط الكاشير')

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

    @property
    def menu_mobile_layout_class(self):
        if self.menu_mobile_layout == self.MenuMobileLayout.LIST:
            return 'layout-list'
        return 'layout-grid-1'

    @property
    def menu_tablet_layout_class(self):
        return f'layout-tablet-{self.menu_tablet_columns}'

    @property
    def menu_desktop_layout_class(self):
        return f'layout-grid-{self.menu_desktop_columns}'

    @property
    def product_card_style_class(self):
        return f'card-{self.product_card_style.replace("_", "-")}'

    @property
    def pos_mobile_layout_class(self):
        return f'layout-grid-{self.pos_mobile_columns}'

    @property
    def pos_tablet_layout_class(self):
        return f'layout-tablet-{self.pos_tablet_columns}'

    @property
    def pos_desktop_layout_class(self):
        return f'layout-grid-{self.pos_desktop_columns}'


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
