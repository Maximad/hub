from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from core.internet_billing import finalize_internet_session
from core.models import Category, HubVisit, InternetSession, Order, Product, SystemSetting
from core.services.hotspot_connect import build_session_hotspot_login_payload
from core.services.visit_internet import (
    finalize_visit_metered_session,
    prepare_visit_metered_session_network,
    start_visit_metered_session,
)
from core.settings_helpers import get_system_settings
from internet.models import InternetSessionNetworkOperation, InternetSessionNetworkState


MIKROTIK_SETTINGS = dict(
    MIKROTIK_ENABLED=True,
    MIKROTIK_BASE_URL='https://router.example.test',
    MIKROTIK_USERNAME='hub-service',
    MIKROTIK_PASSWORD='not-used-in-unit-test',
    MIKROTIK_VERIFY_TLS=True,
    MIKROTIK_CA_FILE='',
    MIKROTIK_CONNECT_TIMEOUT=1,
    MIKROTIK_READ_TIMEOUT=1,
    MIKROTIK_HOTSPOT_SERVER='hub-hotspot',
    MIKROTIK_HOTSPOT_LOGIN_URL='https://wifi.example.test/login',
    MIKROTIK_DEFAULT_PROFILE='hub-full',
    MIKROTIK_USER_PREFIX='hub-',
)


class SuccessfulBackend:
    def provision_access(self, session):
        session.network_status = 'provisioned'
        session.save(update_fields=['network_status', 'updated_at'])
        return session

    def disconnect_access(self, session):
        session.network_status = 'disconnected'
        session.save(update_fields=['network_status', 'updated_at'])
        return session

    def refresh_access(self, session):
        return self.provision_access(session)


class FailingBackend:
    def provision_access(self, session):
        raise ValidationError('router unavailable')


@override_settings(**MIKROTIK_SETTINGS)
class MeteredSafeStartTests(TestCase):
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
        self.visit = HubVisit.objects.create()
        self.credential = SimpleNamespace(visit_id=self.visit.pk)

    def tearDown(self):
        get_system_settings.cache_clear()

    def _start(self, *, at=None):
        return start_visit_metered_session(
            visit=self.visit,
            credential=self.credential,
            at=at,
        )[0]

    def test_failed_provision_never_opens_billing_gate_and_customer_can_cancel_free(self):
        requested = timezone.now() - timedelta(minutes=20)
        session = self._start(at=requested)
        self.assertEqual(session.network_provider, InternetSession.NetworkProvider.MIKROTIK)
        self.assertEqual(session.network_status, 'not_provisioned')
        self.assertEqual(InternetSessionNetworkOperation.objects.count(), 1)

        with patch('internet.session_network_operations.get_session_network_backend',
                   return_value=FailingBackend()):
            self.assertFalse(prepare_visit_metered_session_network(session))

        session.refresh_from_db()
        state = InternetSessionNetworkState.objects.get(session=session)
        self.assertIsNone(state.network_activated_at)
        self.assertEqual(session.status, InternetSession.Status.ACTIVE)
        self.assertEqual(session.network_status, 'provision_error')

        with self.assertRaises(ValidationError):
            finalize_internet_session(
                session,
                None,
                ended_at=requested + timedelta(hours=1),
            )

        ended = finalize_visit_metered_session(
            session,
            at=requested + timedelta(hours=1),
        )
        self.assertEqual(ended.status, InternetSession.Status.CANCELLED)
        self.assertEqual(ended.calculated_total_syp, 0)
        self.assertEqual(ended.billable_minutes, 0)
        self.assertEqual(ended.lifecycle_end_reason, 'network_not_activated')
        self.assertEqual(Order.objects.count(), 0)
        self.assertTrue(InternetSessionNetworkOperation.objects.filter(
            session=ended,
            operation=InternetSessionNetworkOperation.Operation.DISCONNECT,
        ).exists())

    def test_successful_provision_reanchors_clock_then_normal_billing_starts(self):
        requested = timezone.now() - timedelta(minutes=20)
        session = self._start(at=requested)

        with patch('internet.session_network_operations.get_session_network_backend',
                   return_value=SuccessfulBackend()):
            self.assertTrue(prepare_visit_metered_session_network(session))

        session.refresh_from_db()
        state = InternetSessionNetworkState.objects.get(session=session)
        self.assertIsNotNone(state.network_activated_at)
        self.assertEqual(session.started_at, state.network_activated_at)
        self.assertEqual(session.start_time, state.network_activated_at)
        self.assertGreater(session.start_time, requested + timedelta(minutes=15))
        self.assertEqual(session.network_status, 'provisioned')

        ended = finalize_visit_metered_session(
            session,
            at=state.network_activated_at + timedelta(minutes=61),
        )
        ended.refresh_from_db()
        self.assertEqual(ended.status, InternetSession.Status.BILLED)
        self.assertEqual(ended.billable_minutes, 75)
        self.assertEqual(ended.payable_total_syp, 750)
        order = Order.objects.get()
        self.assertEqual(order.total_syp, 750)
        self.assertTrue(InternetSessionNetworkOperation.objects.filter(
            session=ended,
            operation=InternetSessionNetworkOperation.Operation.DISCONNECT,
        ).exists())

    def test_metered_session_can_build_no_store_hotspot_credentials_after_activation(self):
        session = self._start()
        with patch('internet.session_network_operations.get_session_network_backend',
                   return_value=SuccessfulBackend()):
            self.assertTrue(prepare_visit_metered_session_network(session))
        session.refresh_from_db()
        state = InternetSessionNetworkState.objects.get(session=session)
        state.network_credential_encrypted = 'encrypted-placeholder'
        state.save(update_fields=['network_credential_encrypted', 'updated_at'])

        fake_backend = SimpleNamespace(
            connection_credentials=lambda target: ('hub-s-metered', 'secret-password')
        )
        with patch('core.services.hotspot_connect.MikroTikSessionNetworkBackend',
                   return_value=fake_backend):
            payload = build_session_hotspot_login_payload(
                session,
                destination_url='https://hub.example.test/visit/current/',
            )

        self.assertEqual(payload['login_url'], 'https://wifi.example.test/login')
        self.assertEqual(payload['login_origin'], 'https://wifi.example.test')
        self.assertEqual(payload['username'], 'hub-s-metered')
        self.assertEqual(payload['password'], 'secret-password')
        self.assertEqual(payload['destination_url'], 'https://hub.example.test/visit/current/')
