"""An empty purchase must never be reported as fully received."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Purchase


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class EmptyPurchaseReceiptStateTests(TestCase):
    def test_empty_purchase_draft_is_not_fully_received_in_inventory_or_finance(self):
        User = get_user_model()
        admin = User.objects.create_user(username='receipt-empty-admin', phone='+963998002217', role='admin')
        self.client.force_login(admin)
        purchase = Purchase.objects.create(business_date=timezone.localdate())

        for url_name in ('staff_inventory_purchase_detail', 'staff_purchase_detail'):
            with self.subTest(view=url_name):
                response = self.client.get(reverse(url_name, args=[purchase.pk]))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['receipt_rows'], [])
                self.assertFalse(response.context['can_receive_purchase'])
                self.assertContains(response, 'لا توجد بنود مواد في مسودة الشراء بعد')
                self.assertNotContains(response, 'اكتمل استلام جميع المواد')
