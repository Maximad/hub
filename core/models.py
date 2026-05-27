import uuid
from django.conf import settings
from django.db import models


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


class TableArea(TimeStampedModel, PublicCodeModel):
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='tables')
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    qr_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)


class Category(TimeStampedModel, PublicCodeModel):
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    description_ar = models.TextField(blank=True)
    description_en = models.TextField(blank=True)


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


class Order(TimeStampedModel, PublicCodeModel):
    class Status(models.TextChoices):
        NEW = 'new', 'جديد'
        ACCEPTED = 'accepted', 'مقبول'
        PREPARING = 'preparing', 'قيد التحضير'
        READY = 'ready', 'جاهز'
        SERVED = 'served', 'تم التقديم'
        CANCELLED = 'cancelled', 'ملغى'

    table = models.ForeignKey(TableArea, on_delete=models.PROTECT, related_name='orders', null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    notes = models.TextField(blank=True)


class OrderItem(TimeStampedModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='order_items')
    quantity = models.PositiveIntegerField(default=1)
    product_name_ar_snapshot = models.CharField(max_length=120)
    product_name_en_snapshot = models.CharField(max_length=120, blank=True)
    unit_price_syp_snapshot = models.PositiveIntegerField()


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


class Member(TimeStampedModel, PublicCodeModel):
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=30, unique=True)
    balance_syp = models.IntegerField(default=0)
    default_plan = models.ForeignKey('members.MembershipPlan', on_delete=models.SET_NULL, null=True, blank=True)


class InternetPackage(TimeStampedModel, PublicCodeModel):
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    description_ar = models.TextField(blank=True)
    description_en = models.TextField(blank=True)
    duration_minutes = models.PositiveIntegerField()
    price_syp = models.PositiveIntegerField()


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


class Shift(TimeStampedModel, PublicCodeModel):
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='opened_shifts')
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='closed_shifts', null=True, blank=True)
    opened_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True, blank=True)


class ActivityLog(TimeStampedModel):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=120)
    details = models.JSONField(default=dict, blank=True)
