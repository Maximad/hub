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
    is_active = models.BooleanField(default=True)


class Order(TimeStampedModel, PublicCodeModel):
    class Status(models.TextChoices):
        NEW = 'new', 'جديد'
        ACCEPTED = 'accepted', 'مقبول'
        PREPARING = 'preparing', 'قيد التحضير'
        READY = 'ready', 'جاهز'
        SERVED = 'served', 'تم التقديم'
        CANCELLED = 'cancelled', 'ملغى'

    table = models.ForeignKey(TableArea, on_delete=models.PROTECT, related_name='orders')
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


class InternetPackage(TimeStampedModel, PublicCodeModel):
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    description_ar = models.TextField(blank=True)
    description_en = models.TextField(blank=True)
    duration_minutes = models.PositiveIntegerField()
    price_syp = models.PositiveIntegerField()


class InternetSession(TimeStampedModel, PublicCodeModel):
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name='internet_sessions')
    package = models.ForeignKey(InternetPackage, on_delete=models.PROTECT, related_name='sessions')
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
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
