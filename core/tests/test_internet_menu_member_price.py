from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import (
    HubVisit,
    InternetEntitlement,
    InternetPackage,
    Member,
    Order,
    OrderItem,
    Room,
    SystemSetting,
    TableArea,
)
from core.settings_helpers import get_system_settings
from internet.catalog import fulfill_internet_items_for_order
from members.models import MembershipPlan, MembershipSubscription


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class InternetMenuMemberPriceTests(TestCase):
    def setUp(self):
        SystemSetting.objects.create(
            customer_visits_enabled=True,
            customer_internet_self_service_enabled=True,
        )
        get_system_settings.cache_clear()
        self.room = Room.objects.create(name_ar='مشاريب')
        self.table = TableArea.objects.create(room=self.room, name_ar='طاولة عضو')
        self.member = Member.objects.create(name_ar='عضو إنترنت', phone='0999000777')
        self.package = InternetPackage.objects.create(
            name_ar='باقة عضو',
            code='member-menu-package',
            price_syp=500,
            access_mode=InternetPackage.AccessMode.TIMED_SESSION,
            session_minutes_limit=60,
            visible_to_customer=True,
        )
        self.product = self.package.catalog_binding.product
        self.plan = MembershipPlan.objects.create(
            code='internet-menu-plan',
            name_ar='عضوية الإنترنت',
            price_syp=0,
        )
        MembershipSubscription.objects.create(
            member=self.member,
            plan=self.plan,
            starts_at=timezone.now() - timedelta(hours=1),
            status=MembershipSubscription.Status.ACTIVE,
            benefit_snapshot=[{
                'rule_id': 7001,
                'benefit_type': 'internet_member_price',
                'value_decimal': '300',
                'scope_code': self.package.code,
                'priority': 10,
            }],
        )

    def tearDown(self):
        get_system_settings.cache_clear()

    def test_fulfillment_reprices_cart_line_from_internet_member_benefit(self):
        visit = HubVisit.objects.create(table=self.table, member=self.member)
        order = Order.objects.create(
            table=self.table,
            visit=visit,
            member=self.member,
            fulfillment_mode=Order.FulfillmentMode.TABLE,
            service_mode=Order.ServiceMode.TABLE,
        )
        item = OrderItem.objects.create(
            order=order,
            product=self.product,
            quantity=1,
            product_name_ar_snapshot=self.product.name_ar,
            unit_price_syp_snapshot=self.package.price_syp,
            line_total_syp_snapshot=self.package.price_syp,
        )

        fulfill_internet_items_for_order(order)

        item.refresh_from_db()
        entitlement = InternetEntitlement.objects.get(order=order)
        self.assertEqual(item.unit_price_syp_snapshot, 300)
        self.assertEqual(item.line_total_syp_snapshot, 300)
        self.assertEqual(order.total_syp, 300)
        self.assertEqual(entitlement.gross_amount_syp, 300)
        self.assertEqual(entitlement.member_id, self.member.pk)
        self.assertTrue(self.product.not_discountable)
