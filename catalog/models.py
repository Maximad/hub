import uuid
from django.core.exceptions import ValidationError
from django.db import models
from .fields import RelativeOrAbsoluteURLField


class CatalogTimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class MenuSection(CatalogTimeStampedModel):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    description_ar = models.TextField(blank=True)
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    visible_on_qr = models.BooleanField(default=True)
    parent = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='children')

    def __str__(self):
        return self.name_ar


class Tag(CatalogTimeStampedModel):
    TAG_TYPES = [
        ('product', 'Product'), ('event', 'Event'), ('member', 'Member'), ('operational', 'Operational'),
        ('public', 'Public'), ('dietary', 'Dietary'), ('seasonal', 'Seasonal'),
    ]
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    code = models.SlugField(unique=True)
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    tag_type = models.CharField(max_length=40, default='product')
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name_ar


class PrepStation(CatalogTimeStampedModel):
    class StationType(models.TextChoices):
        KITCHEN = 'kitchen', 'المطبخ'
        BAR = 'bar', 'البار'
        CASHIER = 'cashier', 'الكاشير'
        SERVICE = 'service', 'الخدمات'
        GENERAL = 'general', 'عام'

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    code = models.SlugField(unique=True)
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    station_type = models.CharField(max_length=20, choices=StationType.choices, default=StationType.GENERAL)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name_ar']
        verbose_name = 'محطة التحضير'
        verbose_name_plural = 'محطات التحضير'

    def __str__(self):
        return self.name_ar or self.name_en or self.code


class ProductMedia(CatalogTimeStampedModel):
    class MediaType(models.TextChoices):
        IMAGE = 'image', 'Image'
        VIDEO = 'video', 'Video'
        GIF = 'gif', 'Animated GIF'
        EXTERNAL_URL = 'external_url', 'External URL'

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    product = models.ForeignKey('core.Product', on_delete=models.CASCADE, related_name='media')
    media_type = models.CharField(max_length=20, choices=MediaType.choices, default=MediaType.IMAGE)
    url = RelativeOrAbsoluteURLField(max_length=500)
    alt_text_ar = models.CharField(max_length=180, blank=True)
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ['sort_order', '-is_primary', 'created_at']
        indexes = [
            models.Index(fields=['product', 'is_active', 'is_primary', 'sort_order']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['product'],
                condition=models.Q(is_active=True, is_primary=True),
                name='unique_active_primary_product_media',
            ),
        ]
        verbose_name = 'وسائط المنتج'
        verbose_name_plural = 'وسائط المنتجات'

    def __str__(self):
        return f'{self.product} - {self.get_media_type_display()}'

    @property
    def display_alt_text(self):
        return self.alt_text_ar or self.product.name_ar

    @property
    def is_visual_media(self):
        return self.media_type in {self.MediaType.IMAGE, self.MediaType.GIF}


class ProductAvailability(CatalogTimeStampedModel):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    product = models.ForeignKey('core.Product', on_delete=models.CASCADE, related_name='availability_rules')
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    available_from_time = models.TimeField(null=True, blank=True)
    available_until_time = models.TimeField(null=True, blank=True)
    days_of_week = models.JSONField(default=list, blank=True)
    event = models.ForeignKey('events.Event', on_delete=models.SET_NULL, null=True, blank=True)
    vendor_participation = models.ForeignKey('vendors.VendorParticipation', on_delete=models.SET_NULL, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        label = self.starts_at or self.available_from_time or 'قاعدة إتاحة'
        return f'{self.product} — {label}'


class ProductOptionGroup(CatalogTimeStampedModel):
    class SelectionType(models.TextChoices):
        SINGLE = 'single', 'Single'
        MULTIPLE = 'multiple', 'Multiple'

    ITEM_TYPE_CHOICES = [
        ('beverage', 'Beverage'),
        ('food', 'Food'),
        ('service', 'Service'),
        ('event_ticket', 'Event Ticket'),
        ('reservation', 'Reservation'),
        ('membership', 'Membership'),
        ('retail', 'Retail'),
        ('addon', 'Addon'),
    ]
    BEVERAGE_TYPE_CHOICES = [
        ('coffee', 'Coffee'), ('tea', 'Tea'), ('mate', 'Mate'), ('juice', 'Juice'),
        ('zhourat', 'Zhourat'), ('local', 'Local'), ('arak', 'Arak'), ('wine', 'Wine'),
        ('beer', 'Beer'), ('cocktail', 'Cocktail'), ('other', 'Other'),
    ]
    FOOD_TYPE_CHOICES = [
        ('hub_food', 'Hub Food'), ('vendor_food', 'Vendor Food'), ('guest_chef', 'Guest Chef'),
        ('snack', 'Snack'), ('daily_dish', 'Daily Dish'), ('other', 'Other'),
    ]
    SERVICE_TYPE_CHOICES = [
        ('internet', 'Internet'), ('workspace', 'Workspace'), ('table_reservation', 'Table Reservation'),
        ('room_booking', 'Room Booking'), ('workshop', 'Workshop'), ('other', 'Other'),
    ]

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    code = models.SlugField(unique=True)
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    selection_type = models.CharField(max_length=20, choices=SelectionType.choices, default=SelectionType.SINGLE)
    is_required = models.BooleanField(default=False)
    min_selected = models.PositiveIntegerField(default=0)
    max_selected = models.PositiveIntegerField(null=True, blank=True)
    applies_to_item_type = models.CharField('نوع المنتج', max_length=30, choices=ITEM_TYPE_CHOICES, blank=True)
    applies_to_beverage_type = models.CharField('نوع المشروب', max_length=30, choices=BEVERAGE_TYPE_CHOICES, blank=True)
    applies_to_food_type = models.CharField('نوع الطعام', max_length=30, choices=FOOD_TYPE_CHOICES, blank=True)
    applies_to_service_type = models.CharField('نوع الخدمة', max_length=30, choices=SERVICE_TYPE_CHOICES, blank=True)
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort_order', 'name_ar']
        verbose_name = 'مجموعات خيارات المنتجات'
        verbose_name_plural = 'مجموعات خيارات المنتجات'

    def __str__(self):
        return self.name_ar

    def clean(self):
        super().clean()
        errors = {}
        if self.applies_to_beverage_type and self.applies_to_item_type != 'beverage':
            errors['applies_to_beverage_type'] = 'اختر نوع المنتج مشروب قبل تقييد نوع المشروب.'
        if self.applies_to_food_type and self.applies_to_item_type != 'food':
            errors['applies_to_food_type'] = 'اختر نوع المنتج طعام قبل تقييد نوع الطعام.'
        if self.applies_to_service_type and self.applies_to_item_type != 'service':
            errors['applies_to_service_type'] = 'اختر نوع المنتج خدمة قبل تقييد نوع الخدمة.'
        if self.max_selected is not None and self.max_selected < self.min_selected:
            errors['max_selected'] = 'الحد الأقصى يجب أن يكون أكبر من أو يساوي الحد الأدنى.'
        if self.selection_type == self.SelectionType.SINGLE and self.max_selected and self.max_selected > 1:
            errors['max_selected'] = 'مجموعة الاختيار الواحد لا يمكن أن تسمح بأكثر من خيار.'
        if errors:
            raise ValidationError(errors)

    def applies_to_product(self, product):
        checks = (
            ('applies_to_item_type', 'item_type'),
            ('applies_to_beverage_type', 'beverage_type'),
            ('applies_to_food_type', 'food_type'),
            ('applies_to_service_type', 'service_type'),
        )
        return all(
            not getattr(self, group_field) or getattr(self, group_field) == getattr(product, product_field, '')
            for group_field, product_field in checks
        )

    @property
    def applicability_summary(self):
        labels = []
        for field_name in ['applies_to_item_type', 'applies_to_beverage_type', 'applies_to_food_type', 'applies_to_service_type']:
            value = getattr(self, field_name)
            if value:
                labels.append(f'{self._meta.get_field(field_name).verbose_name}: {value}')
        return '، '.join(labels) if labels else 'بدون تقييد'


class ProductOption(CatalogTimeStampedModel):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    group = models.ForeignKey(ProductOptionGroup, on_delete=models.CASCADE, related_name='options')
    code = models.SlugField()
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    price_delta_syp = models.IntegerField(default=0)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name_ar']
        verbose_name = 'خيارات المنتجات'
        verbose_name_plural = 'خيارات المنتجات'
        constraints = [
            models.UniqueConstraint(fields=['group', 'code'], name='unique_option_code_per_group'),
        ]

    def __str__(self):
        return f'{self.group.name_ar} - {self.name_ar}'


class ProductOptionGroupAssignment(CatalogTimeStampedModel):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    product = models.ForeignKey('core.Product', on_delete=models.CASCADE, related_name='option_group_assignments')
    group = models.ForeignKey(ProductOptionGroup, on_delete=models.CASCADE, related_name='product_assignments')
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort_order', 'group__sort_order', 'group__name_ar']
        verbose_name = 'ربط الخيارات بالمنتجات'
        verbose_name_plural = 'ربط الخيارات بالمنتجات'
        constraints = [
            models.UniqueConstraint(fields=['product', 'group'], name='unique_option_group_per_product'),
        ]

    def __str__(self):
        product_name = getattr(self.product, 'name_ar', self.product_id)
        group_name = getattr(self.group, 'name_ar', self.group_id)
        return f'{product_name} - {group_name}'

    def clean(self):
        super().clean()
        if self.is_active and self.product_id and self.group_id and not self.group.applies_to_product(self.product):
            raise ValidationError({'group': 'مجموعة الخيارات لا تنطبق على تصنيف هذا المنتج.'})
