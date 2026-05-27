import uuid
from django.db import models


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


class PrepStation(CatalogTimeStampedModel):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    code = models.SlugField(unique=True)
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)


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
