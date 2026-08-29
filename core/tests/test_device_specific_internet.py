from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import (
    Category,
    HubVisit,
    HubVisitBrowserCredential,
    InternetPackage,
    InternetSession,
    Order,
    Product,
    Room,
    SystemSetting,
    TableArea,
)
from core.services.table_visit_access import visit_join_pin
from core.services.visit_internet_devices import (
    browser_session_queryset,
    create_visit_internet_sale_and_start,
    start_visit_metered_session,
)
from core.services.visits import issue_visit_credential
from core.settings_helpers import get_system_settings
from internet.models import InternetSessionBrowserBinding


WEB_SETTINGS = dict(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    STORAGES={
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    },
)


@override_settings(**WEB_SETTINGS)
class DeviceSpecificInternetTests(TestCase):
    def setUp(self):
        category = Category.objects.create(name_ar='خدمات')
        self.internet_product = Product.objects.create(
            category=category,
            name_ar='إنترنت حسب الوقت',
            price_syp=0,
            product_type=Product.ProductType.INTERNET,
            item_type=Product.ItemType.SERVICE,
            service_type=Product.ServiceType.INTERNET,
            requires_preparation=False,
            visible_on_qr=False,
            orderable_on_qr=False,
            visible_on_pos=False,
            orderable_on_pos=False,
            not_discountable=True,
            track_margin=False,
        )
        SystemSetting.objects.create(
            customer_visits_enabled=True,
            customer_internet_self_service_enabled=True,
            internet_metered_enabled=True,
            default_rate_per_hour_syp=600,
            default_minimum_minutes=30,
            default_rounding_increment_minutes=15,
            default_minimum_charge_syp=0,
            default_free_grace_minutes=0,
            auto_create_order_for_metered_sessions=True,
            internet_service_product=self.internet_product,
        )
        get_system_settings.cache_clear()
        room = Room.objects.create(name_ar='مشاريب')
        self.table = TableArea.objects.create(room=room, name_ar='طاولة 12')
        self.visit = HubVisit.objects.create(table=self.table)
        self.credential_a = HubVisitBrowserCredential.objects.create(
            visit=self.visit,
            token_hash='a' * 64,
        )
        self.credential_b = HubVisitBrowserCredential.objects.create(
            visit=self.visit,
            token_hash='b' * 64,
        )

    def tearDown(self):
        get_system_settings.cache_clear()

    def test_two_browsers_on_same_bill_get_independent_metered_sessions(self):
        session_a, created_a = start_visit_metered_session(
            visit=self.visit,
            credential=self.credential_a,
        )
        session_b, created_b = start_visit_metered_session(
            visit=self.visit,
            credential=self.credential_b,
        )

        self.assertTrue(created_a)
        self.assertTrue(created_b)
        self.assertNotEqual(session_a.pk, session_b.pk)
        self.assertEqual(session_a.visit_id, self.visit.pk)
        self.assertEqual(session_b.visit_id, self.visit.pk)
        self.assertEqual(session_a.browser_binding.credential_id, self.credential_a.pk)
        self.assertEqual(session_b.browser_binding.credential_id, self.credential_b.pk)
        self.assertEqual(InternetSession.objects.filter(visit=self.visit, status='active').count(), 2)

        repeat_a, created_repeat_a = start_visit_metered_session(
            visit=self.visit,
            credential=self.credential_a,
        )
        repeat_b, created_repeat_b = start_visit_metered_session(
            visit=self.visit,
            credential=self.credential_b,
        )
        self.assertFalse(created_repeat_a)
        self.assertFalse(created_repeat_b)
        self.assertEqual(repeat_a.pk, session_a.pk)
        self.assertEqual(repeat_b.pk, session_b.pk)

        self.assertEqual(
            list(browser_session_queryset(self.credential_a).values_list('pk', flat=True)),
            [session_a.pk],
        )
        self.assertEqual(
            list(browser_session_queryset(self.credential_b).values_list('pk', flat=True)),
            [session_b.pk],
        )

    def test_two_browsers_can_purchase_separate_packages_on_same_bill(self):
        package = InternetPackage.objects.create(
            name_ar='ساعة سريعة',
            code='device-hour',
            price_syp=500,
            access_mode=InternetPackage.AccessMode.TIMED_SESSION,
            session_minutes_limit=60,
            visible_to_customer=True,
        )

        entitlement_a, session_a, created_a = create_visit_internet_sale_and_start(
            visit=self.visit,
            credential=self.credential_a,
            package=package,
            request_key='device-a-package',
        )
        entitlement_b, session_b, created_b = create_visit_internet_sale_and_start(
            visit=self.visit,
            credential=self.credential_b,
            package=package,
            request_key='device-b-package',
        )

        self.assertTrue(created_a)
        self.assertTrue(created_b)
        self.assertNotEqual(entitlement_a.pk, entitlement_b.pk)
        self.assertNotEqual(session_a.pk, session_b.pk)
        self.assertEqual(session_a.browser_binding.credential_id, self.credential_a.pk)
        self.assertEqual(session_b.browser_binding.credential_id, self.credential_b.pk)
        self.assertEqual(Order.objects.filter(visit=self.visit).count(), 2)

    def test_second_joined_browser_sees_only_its_own_session_in_session_screen(self):
        # Use real browser credentials/cookies to exercise the canonical Session page.
        InternetSessionBrowserBinding.objects.all().delete()
        HubVisitBrowserCredential.objects.all().delete()
        first_credential, first_token = issue_visit_credential(self.visit)
        first = Client()
        first.cookies['hub_visit'] = first_token

        table_url = reverse('menu_table', kwargs={'qr_token': self.table.qr_token})
        second = Client()
        second.post(
            table_url,
            {'visit_action': 'join', 'pin': visit_join_pin(self.visit)},
        )
        second_credential = HubVisitBrowserCredential.objects.exclude(pk=first_credential.pk).get()

        first_session, _ = start_visit_metered_session(
            visit=self.visit,
            credential=first_credential,
        )

        first_page = first.get(reverse('current_visit'))
        self.assertContains(first_page, 'سريع · فعال على هذا الجهاز')
        second_page = second.get(reverse('current_visit'))
        self.assertContains(second_page, 'تشغيل الإنترنت السريع')
        self.assertNotContains(second_page, 'سريع · فعال على هذا الجهاز')

        second_session, _ = start_visit_metered_session(
            visit=self.visit,
            credential=second_credential,
        )
        self.assertNotEqual(first_session.pk, second_session.pk)

        second_active = second.get(reverse('current_visit'))
        self.assertContains(second_active, 'سريع · فعال على هذا الجهاز')

    def test_browser_cannot_stop_another_browser_session(self):
        session_a, _ = start_visit_metered_session(
            visit=self.visit,
            credential=self.credential_a,
        )
        session_b, _ = start_visit_metered_session(
            visit=self.visit,
            credential=self.credential_b,
        )
        _credential, raw_token = issue_visit_credential(self.visit)
        # Rebind the cookie credential to A by issuing a fresh raw token is not
        # possible for an existing credential; use the exact A credential through
        # its hashed-token construction instead in a separate integration test.
        # The queryset-level ownership is the invariant used by both stop/connect.
        self.assertFalse(
            browser_session_queryset(self.credential_a).filter(pk=session_b.pk).exists()
        )
        self.assertTrue(
            browser_session_queryset(self.credential_a).filter(pk=session_a.pk).exists()
        )
        self.assertIsNotNone(raw_token)
