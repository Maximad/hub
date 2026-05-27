import uuid
from django.db import models


class MembershipPlan(models.Model):
    PERIODS = [('one_time','One Time'),('daily','Daily'),('weekly','Weekly'),('monthly','Monthly'),('yearly','Yearly'),('custom','Custom')]
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    code = models.SlugField(unique=True)
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    description_ar = models.TextField(blank=True)
    billing_period = models.CharField(max_length=20, choices=PERIODS, default='custom')
    price_syp = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class MembershipSubscription(models.Model):
    STATUS = [('active','Active'),('paused','Paused'),('expired','Expired'),('cancelled','Cancelled')]
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    member = models.ForeignKey('core.Member', on_delete=models.CASCADE, related_name='subscriptions')
    plan = models.ForeignKey(MembershipPlan, on_delete=models.PROTECT, related_name='subscriptions')
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default='active')
    remaining_internet_minutes = models.IntegerField(null=True, blank=True)
    remaining_credit_syp = models.IntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class MembershipBenefitRule(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    plan = models.ForeignKey(MembershipPlan, on_delete=models.CASCADE, related_name='benefit_rules')
    item_type = models.CharField(max_length=30, blank=True)
    beverage_type = models.CharField(max_length=30, blank=True)
    food_type = models.CharField(max_length=30, blank=True)
    service_type = models.CharField(max_length=30, blank=True)
    menu_section = models.ForeignKey('catalog.MenuSection', on_delete=models.SET_NULL, null=True, blank=True)
    category = models.ForeignKey('core.Category', on_delete=models.SET_NULL, null=True, blank=True)
    product = models.ForeignKey('core.Product', on_delete=models.SET_NULL, null=True, blank=True)
    tag = models.ForeignKey('catalog.Tag', on_delete=models.SET_NULL, null=True, blank=True)
    discount_percent = models.IntegerField(null=True, blank=True)
    discount_amount_syp = models.IntegerField(null=True, blank=True)
    included_quantity = models.IntegerField(null=True, blank=True)
    included_minutes = models.IntegerField(null=True, blank=True)
    monthly_credit_syp = models.IntegerField(null=True, blank=True)
    applies_to_alcohol = models.BooleanField(default=False)
    priority = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class MemberCreditLedger(models.Model):
    CHANGE_TYPES = [('add_minutes','Add Minutes'),('use_minutes','Use Minutes'),('add_credit','Add Credit'),('use_credit','Use Credit'),('manual_adjustment','Manual Adjustment')]
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    member = models.ForeignKey('core.Member', on_delete=models.CASCADE, related_name='credit_ledger')
    subscription = models.ForeignKey(MembershipSubscription, on_delete=models.SET_NULL, null=True, blank=True)
    change_type = models.CharField(max_length=30, choices=CHANGE_TYPES)
    minutes_delta = models.IntegerField(null=True, blank=True)
    credit_delta_syp = models.IntegerField(null=True, blank=True)
    related_order = models.ForeignKey('core.Order', on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
