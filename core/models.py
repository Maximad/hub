import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import Q


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

    class ProductType(models.TextChoices):
        FOOD = 'food', 'طعام'
        DRINK = 'drink', 'مشروب'
        SERVICE = 'service', 'خدمة'
        INTERNET = 'internet', 'إنترنت'
        EVENT = 'event', 'فعالية'
        MEMBERSHIP = 'membership', 'عضوية'
        OTHER = 'other', 'أخرى'

    product_type = models.CharField('نوع التشغيل', max_length=20, choices=ProductType.choices, default=ProductType.OTHER)
    item_type = models.CharField(max_length=30, choices=ItemType.choices, default=ItemType.BEVERAGE)
    beverage_type = models.CharField(max_length=30, choices=BeverageType.choices, blank=True)
    food_type = models.CharField(max_length=30, choices=FoodType.choices, blank=True)
    service_type = models.CharField(max_length=30, choices=ServiceType.choices, blank=True)
    prep_station_ref = models.ForeignKey('catalog.PrepStation', on_delete=models.SET_NULL, null=True, blank=True, verbose_name='محطة التحضير')
    requires_preparation = models.BooleanField('يحتاج تحضير', default=False)
    visible_on_pos = models.BooleanField('ظاهر في نقطة البيع', default=True)
    orderable_on_pos = models.BooleanField('قابل للطلب من نقطة البيع', default=True)
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

    @property
    def prep_station(self):
        return self.prep_station_ref

    @prep_station.setter
    def prep_station(self, value):
        self.prep_station_ref = value

    def infer_requires_preparation(self):
        return self.requires_preparation or self.product_type in {self.ProductType.FOOD, self.ProductType.DRINK} or self.item_type in {self.ItemType.FOOD, self.ItemType.BEVERAGE}

    def __str__(self):
        return _arabic_first(self, 'name_ar', 'name_en', fallback=str(self.public_code)[:8])


class CancellationReason(models.TextChoices):
    CUSTOMER_CHANGED_MIND = 'customer_changed_mind', 'غيّر الزبون رأيه'
    ITEM_UNAVAILABLE = 'item_unavailable', 'المنتج غير متوفر'
    STAFF_MISTAKE = 'staff_mistake', 'خطأ من الموظف'
    DUPLICATE_ORDER = 'duplicate_order', 'طلب مكرر'
    QUALITY_ISSUE = 'quality_issue', 'مشكلة جودة'
    MANAGER_DECISION = 'manager_decision', 'قرار المدير'
    OTHER = 'other', 'سبب آخر'


class Order(TimeStampedModel, PublicCodeModel):
    class ServiceMode(models.TextChoices):
        DINE_IN = 'dine_in', 'طلب عام داخل المكان'
        TABLE = 'table', 'طلب طاولة'
        TAKEAWAY = 'takeaway', 'تيك أواي'

    class FulfillmentMode(models.TextChoices):
        INSIDE_SPACE = 'inside_space', 'طلب داخل المكان'
        TABLE = 'table', 'طاولة'
        DELIVERY = 'delivery', 'توصيل'
        TAKEAWAY = 'takeaway', 'تيك أواي'

    class DeliveryStatus(models.TextChoices):
        NOT_DELIVERY = 'not_delivery', 'ليس توصيل'
        DELIVERY_REQUESTED = 'delivery_requested', 'طلب توصيل'
        CONFIRMED = 'confirmed', 'مؤكد'
        PREPARING = 'preparing', 'قيد التحضير'
        READY_FOR_DISPATCH = 'ready_for_dispatch', 'جاهز للإرسال'
        OUT_FOR_DELIVERY = 'out_for_delivery', 'خرج للتوصيل'
        DELIVERED = 'delivered', 'تم التسليم'
        FAILED = 'failed', 'فشل التوصيل'
        CANCELLED = 'cancelled', 'ملغى'

    class Status(models.TextChoices):
        NEW = 'new', 'جديد'
        ACCEPTED = 'accepted', 'مقبول'
        PREPARING = 'preparing', 'قيد التحضير'
        READY = 'ready', 'جاهز'
        SERVED = 'served', 'تم التقديم'
        CANCELLED = 'cancelled', 'ملغى'

    table = models.ForeignKey(TableArea, on_delete=models.PROTECT, related_name='orders', null=True, blank=True)
    service_mode = models.CharField(max_length=20, choices=ServiceMode.choices, default=ServiceMode.DINE_IN)
    fulfillment_mode = models.CharField(max_length=20, choices=FulfillmentMode.choices, default=FulfillmentMode.INSIDE_SPACE, verbose_name='وضع الاستلام/التنفيذ')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    notes = models.TextField(blank=True)
    delivery_address = models.TextField(blank=True, verbose_name='عنوان التوصيل')
    delivery_area = models.CharField(max_length=120, blank=True, verbose_name='منطقة التوصيل')
    delivery_notes = models.TextField(blank=True, verbose_name='ملاحظات التوصيل')
    delivery_fee_syp = models.PositiveIntegerField(default=0, verbose_name='رسوم التوصيل')
    delivery_eta_minutes = models.PositiveIntegerField(null=True, blank=True, verbose_name='زمن التوصيل المتوقع بالدقائق')
    delivery_status = models.CharField(max_length=30, choices=DeliveryStatus.choices, default=DeliveryStatus.NOT_DELIVERY, verbose_name='حالة التوصيل')
    cancellation_reason = models.CharField(max_length=30, choices=CancellationReason.choices, blank=True, verbose_name='سبب الإلغاء')
    cancellation_notes = models.TextField(blank=True, verbose_name='ملاحظات الإلغاء')
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='cancelled_orders', verbose_name='ألغاه')
    cancelled_at = models.DateTimeField(null=True, blank=True, verbose_name='وقت الإلغاء')
    assigned_driver_name = models.CharField(max_length=120, blank=True, verbose_name='اسم السائق')
    assigned_driver_phone = models.CharField(max_length=30, blank=True, verbose_name='هاتف السائق')

    @property
    def display_number(self):
        if self.pk:
            return f'#{self.pk:05d}'
        return f'#{str(self.public_code)[:8].upper()}'

    @property
    def location_label(self):
        if self.fulfillment_mode == self.FulfillmentMode.TABLE or self.table_id:
            return f'الطاولة: {self.table.name_ar}' if self.table_id else 'طاولة'
        if self.fulfillment_mode == self.FulfillmentMode.DELIVERY:
            return 'توصيل'
        if self.fulfillment_mode == self.FulfillmentMode.TAKEAWAY or self.service_mode == self.ServiceMode.TAKEAWAY:
            return 'تيك أواي'
        return 'طلب داخل المكان'

    @property
    def location_detail(self):
        if self.table_id and self.table.room_id:
            return f'المساحة: {self.table.room.name_ar}'
        if self.is_delivery and self.delivery_area:
            return self.delivery_area
        return ''

    def get_fulfillment_label(self):
        if self.is_table_order and self.table_id:
            return f'الطاولة: {self.table.name_ar}'
        return self.FulfillmentMode(self.fulfillment_mode).label if self.fulfillment_mode in self.FulfillmentMode.values else 'طلب داخل المكان'

    @property
    def is_delivery(self):
        return self.fulfillment_mode == self.FulfillmentMode.DELIVERY

    @property
    def is_table_order(self):
        return self.fulfillment_mode == self.FulfillmentMode.TABLE or bool(self.table_id)

    @property
    def delivery_fee_display(self):
        return f'{max(self.delivery_fee_syp or 0, 0)} ل.س'

    @property
    def subtotal_syp(self):
        total = 0
        for item in self.items.all():
            line_total = item.line_total_syp_snapshot
            if line_total is None:
                line_total = item.quantity * item.unit_price_syp_snapshot
            total += line_total
        return max(total, 0)

    @property
    def discount_syp(self):
        return min(sum(d.amount_syp for d in self.discounts.all() if d.is_active), self.subtotal_syp + self.service_fee_syp + self.delivery_fee_syp)

    @property
    def service_fee_syp(self):
        return 0

    @property
    def total_syp(self):
        return max(self.subtotal_syp + self.service_fee_syp + max(self.delivery_fee_syp or 0, 0) - self.discount_syp, 0)

    @property
    def total_with_delivery_syp(self):
        return self.total_syp

    @property
    def paid_syp(self):
        return sum(payment.amount_syp for payment in self.payments.all() if payment.is_active and not payment.is_reversed and payment.method != Payment.Method.UNPAID)

    @property
    def remaining_syp(self):
        return max(self.total_syp - self.paid_syp, 0)

    @property
    def payment_status(self):
        if self.status == self.Status.CANCELLED:
            return 'cancelled'
        if self.paid_syp <= 0:
            return 'unpaid'
        if self.remaining_syp > 0:
            return 'partially_paid'
        return 'paid'

    def __str__(self):
        return self.display_number


class OrderItem(TimeStampedModel):
    class PrepStatus(models.TextChoices):
        NEW = 'new', 'جديد'
        SENT = 'sent', 'مرسل للتحضير'
        ACCEPTED = 'accepted', 'تم الاستلام'
        PREPARING = 'preparing', 'قيد التحضير'
        READY = 'ready', 'جاهز'
        SERVED = 'served', 'تم التسليم'
        CANCELLED = 'cancelled', 'ملغى'
        NO_PREP = 'no_prep', 'لا يحتاج تحضير'
        PENDING = 'pending', 'جديد'

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='order_items')
    quantity = models.PositiveIntegerField(default=1)
    product_name_ar_snapshot = models.CharField(max_length=120)
    product_name_en_snapshot = models.CharField(max_length=120, blank=True)
    unit_price_syp_snapshot = models.PositiveIntegerField()
    selected_options_snapshot = models.JSONField(default=list, blank=True)
    item_note = models.TextField(blank=True)
    line_total_syp_snapshot = models.IntegerField(null=True, blank=True)
    prep_station = models.ForeignKey('catalog.PrepStation', on_delete=models.SET_NULL, null=True, blank=True, related_name='order_items', verbose_name='محطة التحضير')
    prep_status = models.CharField(max_length=20, choices=PrepStatus.choices, default=PrepStatus.NEW)
    cancellation_reason = models.CharField(max_length=30, choices=CancellationReason.choices, blank=True, verbose_name='سبب الإلغاء')
    cancellation_notes = models.TextField(blank=True, verbose_name='ملاحظات الإلغاء')

    def assign_prep_defaults(self):
        requires_prep = self.product.infer_requires_preparation() if self.product_id else True
        if not requires_prep:
            self.prep_status = self.PrepStatus.NO_PREP
            return
        if not self.prep_station_id and self.product_id:
            self.prep_station = self.product.prep_station_ref
            if self.prep_station is None and requires_prep:
                from catalog.models import PrepStation
                self.prep_station = (
                    PrepStation.objects.filter(code='general', is_active=True).first()
                    or PrepStation.objects.filter(station_type='general', is_active=True).first()
                )
        if self.prep_status in {'', self.PrepStatus.NO_PREP}:
            self.prep_status = self.PrepStatus.NEW

    def save(self, *args, **kwargs):
        if self.product_id and (not self.prep_station_id or not self.prep_status):
            self.assign_prep_defaults()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.product_name_ar_snapshot} × {self.quantity} — {self.order.display_number}'




class NotificationEvent(TimeStampedModel):
    class EventType(models.TextChoices):
        NEW_ORDER = 'new_order', 'طلب جديد'
        ORDER_EDITED = 'order_edited', 'تم تعديل الطلب'
        ORDER_CANCELLED = 'order_cancelled', 'تم إلغاء الطلب'
        NEW_PREP_ITEM = 'new_prep_item', 'عنصر جديد للتحضير'
        PREP_ITEM_READY = 'prep_item_ready', 'عنصر جاهز'
        PREP_ITEM_CANCELLED = 'prep_item_cancelled', 'تم إلغاء عنصر'
        PAYMENT_PENDING = 'payment_pending', 'الدفع بانتظار الكاشير'
        PARTIAL_PAYMENT_REQUESTED = 'partial_payment_requested', 'دفع جزئي يحتاج موافقة'
        DISCOUNT_ADDED = 'discount_added', 'تمت إضافة خصم'
        MANAGER_APPROVAL_NEEDED = 'manager_approval_needed', 'مطلوب موافقة المدير'
        CLOSE_DAY_FINALIZED = 'close_day_finalized', 'تم إغلاق اليوم'
        DELIVERY_ORDER_CREATED = 'delivery_order_created', 'طلب توصيل جديد'

    event_type = models.CharField(max_length=40, choices=EventType.choices)
    title_ar = models.CharField(max_length=160)
    message_ar = models.TextField(blank=True)
    order = models.ForeignKey('Order', on_delete=models.SET_NULL, null=True, blank=True, related_name='notification_events')
    order_item = models.ForeignKey('OrderItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='notification_events')
    target_station = models.ForeignKey('catalog.PrepStation', on_delete=models.SET_NULL, null=True, blank=True, related_name='notification_events')
    target_role = models.CharField(max_length=30, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_notification_events')
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['event_type', 'created_at']), models.Index(fields=['target_role', 'created_at'])]

    def __str__(self):
        order = f' {self.order.display_number}' if self.order_id else ''
        return f'{self.title_ar}{order}'


class NotificationRecipient(TimeStampedModel):
    notification_event = models.ForeignKey(NotificationEvent, on_delete=models.CASCADE, related_name='recipients')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name='notification_recipients')
    role = models.CharField(max_length=30, blank=True)
    station = models.ForeignKey('catalog.PrepStation', on_delete=models.SET_NULL, null=True, blank=True, related_name='notification_recipients')
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', 'is_read', 'created_at']), models.Index(fields=['role', 'is_read', 'created_at'])]

    def __str__(self):
        return str(self.notification_event)


class NotificationPreference(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notification_preference')
    enable_sound = models.BooleanField(default=True)
    enable_browser_notifications = models.BooleanField(default=False)
    notify_new_orders = models.BooleanField(default=True)
    notify_prep_items = models.BooleanField(default=True)
    notify_payment_alerts = models.BooleanField(default=True)
    notify_manager_approvals = models.BooleanField(default=True)
    notify_daily_close = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'تنبيهات {self.user}'


class NotificationLog(TimeStampedModel):
    class Channel(models.TextChoices):
        SYSTEM = 'system', 'System'
        SOUND = 'sound', 'Sound'
        BROWSER = 'browser', 'Browser'
        TELEGRAM = 'telegram', 'Telegram'
        WHATSAPP = 'whatsapp', 'WhatsApp'
        SMS = 'sms', 'SMS'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SENT = 'sent', 'Sent'
        FAILED = 'failed', 'Failed'
        SKIPPED = 'skipped', 'Skipped'

    notification_event = models.ForeignKey(NotificationEvent, on_delete=models.CASCADE, related_name='logs')
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.SYSTEM)
    recipient_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='notification_logs')
    recipient_role = models.CharField(max_length=30, blank=True)
    recipient_station = models.ForeignKey('catalog.PrepStation', on_delete=models.SET_NULL, null=True, blank=True, related_name='notification_logs')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    error_message = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.notification_event} — {self.channel} — {self.status}'


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
    notes = models.TextField(blank=True, verbose_name='ملاحظات الدفع')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_payments')
    is_active = models.BooleanField(default=True)
    is_reversed = models.BooleanField(default=False)
    reversal_reason = models.CharField(max_length=30, choices=CancellationReason.choices, blank=True, verbose_name='سبب عكس الدفعة')
    reversed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='reversed_payments')
    reversed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.order.display_number} — {self.get_method_display()} — {self.amount_syp} ل.س'


class OrderDiscount(TimeStampedModel):
    class DiscountType(models.TextChoices):
        FIXED = 'fixed', 'مبلغ ثابت'
        PERCENTAGE = 'percentage', 'نسبة مئوية'
        COMP = 'comp', 'ضيافة'
        CORRECTION = 'correction', 'تصحيح'
        MEMBER = 'member', 'عضو'

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='discounts')
    discount_type = models.CharField(max_length=20, choices=DiscountType.choices, default=DiscountType.FIXED, verbose_name='نوع الخصم')
    amount_syp = models.PositiveIntegerField(verbose_name='الخصم')
    percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(100)], verbose_name='النسبة')
    reason = models.CharField(max_length=255, verbose_name='سبب الخصم')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_order_discounts')
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_order_discounts', verbose_name='موافقة المدير')
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    def clean(self):
        if not (self.reason or '').strip():
            raise ValidationError({'reason': 'سبب الخصم مطلوب.'})
        if self.amount_syp < 0:
            raise ValidationError({'amount_syp': 'الخصم لا يمكن أن يكون سالباً.'})
        base = self.order.subtotal_syp + self.order.service_fee_syp + max(self.order.delivery_fee_syp or 0, 0) if self.order_id else 0
        existing = 0
        if self.order_id:
            qs = self.order.discounts.filter(is_active=True)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            existing = sum(d.amount_syp for d in qs)
        if self.amount_syp + existing > base:
            raise ValidationError({'amount_syp': 'الخصم لا يجوز أن يجعل المجموع سالباً.'})

    def __str__(self):
        return f'{self.order.display_number} — {self.amount_syp} ل.س'


class DailyClose(TimeStampedModel):
    business_date = models.DateField(unique=True, verbose_name='تاريخ العمل')
    opening_cash_syp = models.PositiveIntegerField(default=0, verbose_name='النقد الافتتاحي')
    cash_sales_syp = models.PositiveIntegerField(default=0, verbose_name='مبيعات نقدية')
    non_cash_sales_syp = models.PositiveIntegerField(default=0, verbose_name='مبيعات غير نقدية')
    total_payments_syp = models.PositiveIntegerField(default=0, verbose_name='إجمالي الدفعات')
    unpaid_orders_syp = models.PositiveIntegerField(default=0, verbose_name='غير مدفوع')
    partial_payments_syp = models.PositiveIntegerField(default=0, verbose_name='مدفوع جزئياً')
    discounts_syp = models.PositiveIntegerField(default=0, verbose_name='الخصومات')
    cancelled_orders_syp = models.PositiveIntegerField(default=0, verbose_name='قيمة الطلبات الملغاة')
    refunds_or_reversals_syp = models.PositiveIntegerField(default=0, verbose_name='المسترد/المعكوس')
    expected_cash_syp = models.PositiveIntegerField(default=0, verbose_name='النقد المتوقع')
    actual_cash_counted_syp = models.PositiveIntegerField(default=0, verbose_name='النقد الفعلي')
    cash_difference_syp = models.IntegerField(default=0, verbose_name='الفرق')
    notes = models.TextField(blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='daily_closes')
    closed_at = models.DateTimeField(null=True, blank=True)
    is_finalized = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['business_date'], condition=Q(is_finalized=True), name='unique_finalized_daily_close_per_date')]

    def __str__(self):
        return f'إغلاق اليوم {self.business_date}'


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
    class BillingMode(models.TextChoices):
        PREPAID = 'prepaid', 'مسبق الدفع'
        OPEN_METERED = 'open_metered', 'مفتوح محسوب بالوقت'
        FREE = 'free', 'مجاني'
        MANUAL = 'manual', 'يدوي'

    class Status(models.TextChoices):
        ACTIVE = 'active', 'فعالة'
        ENDED = 'ended', 'منتهية'
        CANCELLED = 'cancelled', 'ملغاة'
        UNPAID = 'unpaid', 'غير مدفوعة'
        PAID = 'paid', 'مدفوعة'

    class NetworkProvider(models.TextChoices):
        MANUAL = 'manual', 'يدوي'
        MIKROTIK = 'mikrotik', 'MikroTik'
        UNIFI = 'unifi', 'UniFi'
        RADIUS = 'radius', 'RADIUS'

    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name='internet_sessions', null=True, blank=True)
    package = models.ForeignKey(InternetPackage, on_delete=models.PROTECT, related_name='sessions', null=True, blank=True)
    customer_name = models.CharField(max_length=120, blank=True)
    customer_phone = models.CharField(max_length=30, blank=True)
    guest_name = models.CharField(max_length=120, blank=True)
    guest_phone = models.CharField(max_length=30, blank=True)
    billing_mode = models.CharField(max_length=20, choices=BillingMode.choices, default=BillingMode.OPEN_METERED)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    actual_duration_minutes = models.PositiveIntegerField(null=True, blank=True)
    duration_minutes = models.PositiveIntegerField(null=True, blank=True)
    rate_per_hour_syp = models.PositiveIntegerField(default=0)
    minimum_minutes = models.PositiveIntegerField(default=0)
    free_grace_minutes = models.PositiveIntegerField(default=0)
    daily_cap_syp = models.PositiveIntegerField(null=True, blank=True)
    calculated_total_syp = models.PositiveIntegerField(default=0)
    manual_total_syp = models.PositiveIntegerField(null=True, blank=True)
    override_reason = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    linked_order = models.ForeignKey(Order, on_delete=models.SET_NULL, related_name='internet_sessions', null=True, blank=True)
    linked_payment = models.ForeignKey(Payment, on_delete=models.SET_NULL, related_name='internet_sessions', null=True, blank=True)
    started_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name='started_internet_sessions', null=True, blank=True)
    ended_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name='ended_internet_sessions', null=True, blank=True)
    notes = models.TextField(blank=True)
    consumed = models.BooleanField(default=False)
    network_provider = models.CharField(max_length=20, choices=NetworkProvider.choices, default=NetworkProvider.MANUAL)
    access_code = models.CharField(max_length=120, blank=True)
    network_session_id = models.CharField(max_length=120, blank=True)
    device_mac = models.CharField(max_length=64, blank=True)
    ip_address = models.CharField(max_length=64, blank=True)
    bandwidth_profile = models.CharField(max_length=120, blank=True)
    network_status = models.CharField(max_length=120, blank=True)

    @property
    def display_guest_name(self):
        return self.guest_name or self.customer_name

    @property
    def display_guest_phone(self):
        return self.guest_phone or self.customer_phone

    @property
    def effective_started_at(self):
        return self.started_at or self.start_time

    @property
    def effective_ended_at(self):
        return self.ended_at or self.end_time

    @property
    def effective_duration_minutes(self):
        return self.duration_minutes if self.duration_minutes is not None else self.actual_duration_minutes

    @property
    def payable_total_syp(self):
        return self.manual_total_syp if self.manual_total_syp is not None else self.calculated_total_syp

    def save(self, *args, **kwargs):
        if self.started_at is None and self.start_time is not None:
            self.started_at = self.start_time
        if self.start_time is None and self.started_at is not None:
            self.start_time = self.started_at
        if not self.guest_name and self.customer_name:
            self.guest_name = self.customer_name
        if not self.customer_name and self.guest_name:
            self.customer_name = self.guest_name
        if not self.guest_phone and self.customer_phone:
            self.guest_phone = self.customer_phone
        if not self.customer_phone and self.guest_phone:
            self.customer_phone = self.guest_phone
        super().save(*args, **kwargs)

    def __str__(self):
        customer = self.member or self.display_guest_name or self.display_guest_phone or 'زائر'
        package = self.package or self.get_billing_mode_display()
        started = self.effective_started_at
        stamp = started.strftime('%Y-%m-%d %H:%M') if started else 'بدون وقت'
        return f'{customer} — {package} — {stamp}'


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

    class DefaultFulfillmentMode(models.TextChoices):
        INSIDE_SPACE = 'inside_space', 'طلب داخل المكان'
        TABLE = 'table', 'طاولة'
        DELIVERY = 'delivery', 'توصيل'
        TAKEAWAY = 'takeaway', 'تيك أواي'

    class DeliveryFeeMode(models.TextChoices):
        NONE = 'none', 'بدون رسوم'
        FIXED = 'fixed', 'رسوم ثابتة'
        MANUAL = 'manual', 'إدخال يدوي'

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

    class InternetBillingMode(models.TextChoices):
        PREPAID = 'prepaid', 'مسبق الدفع'
        OPEN_METERED = 'open_metered', 'مفتوح محسوب بالوقت'
        FREE = 'free', 'مجاني'
        MANUAL = 'manual', 'يدوي'

    system_title_ar = models.CharField(max_length=160, default='نظام مشاريب', verbose_name='عنوان النظام بالعربية')
    system_title_en = models.CharField(max_length=160, default='Masharib System', verbose_name='عنوان النظام بالإنجليزية')
    public_brand_title_ar = models.CharField(max_length=160, default='مشاريب', verbose_name='اسم العلامة في المنيو')
    public_brand_title_en = models.CharField(max_length=160, default='Masharib', verbose_name='اسم العلامة بالإنجليزية')
    header_subtitle_ar = models.CharField(max_length=220, default='Hub Sueda • تشغيل يومي مرن', verbose_name='وصف الهيدر بالعربية')
    header_subtitle_en = models.CharField(max_length=220, default='Hub Sueda • flexible daily operations', verbose_name='وصف الهيدر بالإنجليزية')
    default_language = models.CharField(max_length=5, choices=Language.choices, default=Language.ARABIC, verbose_name='اللغة الافتراضية')
    enable_delivery = models.BooleanField(default=False, verbose_name='تفعيل التوصيل', help_text='يبقى خيار التوصيل مخفياً في المنيو وPOS ما لم يتم تفعيله.')
    enable_takeaway = models.BooleanField(default=False, verbose_name='تفعيل التيك أواي', help_text='يبقى خيار التيك أواي مخفياً في المنيو وPOS ما لم يتم تفعيله.')
    enable_table_orders = models.BooleanField(default=True, verbose_name='تفعيل طلبات الطاولات')
    enable_general_in_space_orders = models.BooleanField(default=True, verbose_name='تفعيل الطلب العام داخل المكان')
    show_internal_order_uuid = models.BooleanField(default=False, verbose_name='إظهار رقم UUID الداخلي')
    default_order_mode = models.CharField(max_length=20, choices=DefaultOrderMode.choices, default=DefaultOrderMode.DINE_IN, verbose_name='وضع الطلب الافتراضي')
    default_fulfillment_mode = models.CharField(max_length=20, choices=DefaultFulfillmentMode.choices, default=DefaultFulfillmentMode.INSIDE_SPACE, verbose_name='وضع التنفيذ الافتراضي')
    require_phone_for_delivery = models.BooleanField(default=True, verbose_name='طلب الهاتف للتوصيل')
    require_address_for_delivery = models.BooleanField(default=True, verbose_name='طلب العنوان للتوصيل')
    delivery_fee_mode = models.CharField(max_length=20, choices=DeliveryFeeMode.choices, default=DeliveryFeeMode.NONE, verbose_name='طريقة رسوم التوصيل')
    fixed_delivery_fee_syp = models.PositiveIntegerField(default=0, verbose_name='رسوم التوصيل الثابتة')
    minimum_delivery_order_syp = models.PositiveIntegerField(default=0, verbose_name='الحد الأدنى لطلب التوصيل')
    delivery_working_hours_text = models.CharField(max_length=240, blank=True, verbose_name='ساعات عمل التوصيل')
    delivery_contact_phone = models.CharField(max_length=40, blank=True, verbose_name='هاتف التواصل للتوصيل')
    delivery_contact_whatsapp = models.CharField(max_length=40, blank=True, verbose_name='واتساب التوصيل')
    delivery_notes = models.TextField(blank=True, verbose_name='ملاحظات التوصيل')

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

    default_internet_billing_mode = models.CharField(max_length=20, choices=InternetBillingMode.choices, default=InternetBillingMode.OPEN_METERED, verbose_name='وضع فوترة الإنترنت الافتراضي')
    default_rate_per_hour_syp = models.PositiveIntegerField(default=0, verbose_name='سعر الساعة الافتراضي للإنترنت/العمل')
    default_minimum_minutes = models.PositiveIntegerField(default=30, verbose_name='الحد الأدنى الافتراضي للدقائق')
    default_free_grace_minutes = models.PositiveIntegerField(default=0, verbose_name='دقائق السماح المجانية الافتراضية')
    default_daily_cap_syp = models.PositiveIntegerField(null=True, blank=True, verbose_name='السقف اليومي الافتراضي')
    allow_guest_internet_sessions = models.BooleanField(default=True, verbose_name='السماح بجلسات الزوار')
    allow_member_internet_sessions = models.BooleanField(default=True, verbose_name='السماح بجلسات الأعضاء')
    auto_create_order_for_metered_sessions = models.BooleanField(default=False, verbose_name='إنشاء طلب تلقائي للجلسات المحسوبة')
    internet_service_product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name='internet_setting_profiles', verbose_name='منتج خدمة الإنترنت للطلبات')
    require_phone_for_guest_session = models.BooleanField(default=False, verbose_name='طلب هاتف الزائر عند بدء الجلسة')

    class Meta:
        verbose_name = 'إعدادات النظام'
        verbose_name_plural = 'إعدادات النظام'

    def __str__(self):
        return self.system_title_ar or self.system_title_en

    def save(self, *args, **kwargs):
        if self.default_fulfillment_mode == self.DefaultFulfillmentMode.DELIVERY and not self.enable_delivery:
            self.default_fulfillment_mode = self.DefaultFulfillmentMode.INSIDE_SPACE
        if self.default_fulfillment_mode == self.DefaultFulfillmentMode.TAKEAWAY and not self.enable_takeaway:
            self.default_fulfillment_mode = self.DefaultFulfillmentMode.INSIDE_SPACE
        self.fixed_delivery_fee_syp = max(self.fixed_delivery_fee_syp or 0, 0)
        self.minimum_delivery_order_syp = max(self.minimum_delivery_order_syp or 0, 0)
        if not self.pk and SystemSetting.objects.exists():
            self.pk = SystemSetting.objects.order_by('-updated_at', '-pk').first().pk
        super().save(*args, **kwargs)

    def available_fulfillment_modes(self, include_table=True):
        modes = [Order.FulfillmentMode.INSIDE_SPACE]
        if include_table and self.enable_table_orders:
            modes.append(Order.FulfillmentMode.TABLE)
        if self.enable_delivery:
            modes.append(Order.FulfillmentMode.DELIVERY)
        if self.enable_takeaway:
            modes.append(Order.FulfillmentMode.TAKEAWAY)
        return modes

    @property
    def safe_default_fulfillment_mode(self):
        mode = self.default_fulfillment_mode or self.DefaultFulfillmentMode.INSIDE_SPACE
        if mode == self.DefaultFulfillmentMode.DELIVERY and not self.enable_delivery:
            return Order.FulfillmentMode.INSIDE_SPACE
        if mode == self.DefaultFulfillmentMode.TAKEAWAY and not self.enable_takeaway:
            return Order.FulfillmentMode.INSIDE_SPACE
        return mode

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
