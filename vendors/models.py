import uuid
from django.db import models
from events.models import Event


class Vendor(models.Model):
    class VendorType(models.TextChoices):
        FOOD_LAB = 'food_lab', 'Food Lab'
        GUEST_CHEF = 'guest_chef', 'Guest Chef'
        PRODUCER = 'producer', 'Producer'
        ARTIST = 'artist', 'Artist'
        TRAINER = 'trainer', 'Trainer'
        SUPPLIER = 'supplier', 'Supplier'
        OTHER = 'other', 'Other'

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    vendor_type = models.CharField(max_length=30, choices=VendorType.choices, default=VendorType.OTHER)
    contact_person = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    commission_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    commission_fixed_syp = models.PositiveIntegerField(null=True, blank=True)
    settlement_notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name_ar or self.name_en or self.phone or str(self.uuid)[:8]


class VendorParticipation(models.Model):
    class Status(models.TextChoices):
        PLANNED = 'planned', 'Planned'
        ACTIVE = 'active', 'Active'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='participations')
    title_ar = models.CharField(max_length=200)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    location_area = models.ForeignKey('core.TableArea', on_delete=models.SET_NULL, null=True, blank=True)
    event = models.ForeignKey(Event, on_delete=models.SET_NULL, null=True, blank=True, related_name='vendor_participations')
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        vendor_name = self.vendor.name_ar if self.vendor_id else ''
        return f'{self.title_ar} — {vendor_name}' if vendor_name else self.title_ar
