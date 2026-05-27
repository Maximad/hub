from django.contrib import admin
from .models import MembershipPlan, MembershipSubscription, MembershipBenefitRule, MemberCreditLedger

admin.site.register([MembershipPlan, MembershipSubscription, MembershipBenefitRule, MemberCreditLedger])
