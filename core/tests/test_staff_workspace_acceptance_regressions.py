from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import HubVisit, Order, Room, TableArea


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class StaffWorkspaceAcceptanceRegressionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='workspace-acceptance-admin',
            phone='92991',
            password='x',
            role='admin',
        )
        self.room = Room.objects.create(name_ar='صالة الاختبار')
        self.table = TableArea.objects.create(room=self.room, name_ar='طاولة الاختبار')
        self.visit = HubVisit.objects.create(table=self.table)
        self.order = Order.objects.create(
            table=self.table,
            visit=self.visit,
            status=Order.Status.READY,
            fulfillment_mode=Order.FulfillmentMode.TABLE,
            service_mode=Order.ServiceMode.TABLE,
        )
        self.client.force_login(self.admin)

    def test_closed_visit_order_is_not_left_in_active_operations(self):
        open_response = self.client.get(reverse('staff_home'))
        self.assertEqual(open_response.status_code, 200)
        self.assertEqual(open_response.context['workspace_stats']['active_orders'], 1)
        self.assertContains(open_response, self.order.display_number)

        self.visit.status = HubVisit.Status.CLOSED
        self.visit.closed_at = timezone.now()
        self.visit.save(update_fields=['status', 'closed_at', 'updated_at'])

        closed_response = self.client.get(reverse('staff_home'))
        self.assertEqual(closed_response.status_code, 200)
        self.assertEqual(closed_response.context['workspace_stats']['open_visits'], 0)
        self.assertEqual(closed_response.context['workspace_stats']['active_orders'], 0)
        self.assertNotContains(closed_response, self.order.display_number)

    def test_settle_form_keeps_styled_full_page_fallback(self):
        panel_url = reverse(
            'staff_cashier_order',
            kwargs={'public_code': self.visit.public_code},
        )
        pay_url = reverse(
            'staff_cashier_pay',
            kwargs={'public_code': self.visit.public_code},
        )

        response = self.client.get(panel_url, {'panel': 'payment'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'action="{pay_url}"')
        self.assertContains(response, f'hx-post="{pay_url}?panel=payment"')
        self.assertNotContains(response, f'action="{pay_url}?panel=payment"')
