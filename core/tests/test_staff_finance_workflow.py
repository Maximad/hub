from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.finance import finalize_daily_close, reopen_daily_close
from core.models import ActivityLog, DailyClose, DailyCloseRevision, InventoryItem, Purchase, PurchaseItem, StockMovement


class StaffFinanceWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='finance-admin', password='pass', phone='+9901', role='admin')
        self.client.force_login(self.user)
        self.item = InventoryItem.objects.create(name_ar='قهوة', unit=InventoryItem.Unit.KG, current_quantity=Decimal('0'))

    def test_reopen_requires_reason_and_preserves_revision(self):
        close, created = finalize_daily_close(date(2026, 7, 17), self.user, 1000)
        self.assertTrue(created)
        with self.assertRaises(ValueError):
            reopen_daily_close(close, self.user, '')
        reopened = reopen_daily_close(close, self.user, 'تصحيح فاتورة')
        self.assertEqual(reopened.status, DailyClose.Status.REOPENED)
        self.assertEqual(DailyCloseRevision.objects.filter(daily_close=close, revision_type='before_reopen').count(), 1)
        self.assertTrue(ActivityLog.objects.filter(action='daily_close_reopened').exists())

    def test_reopened_close_can_be_closed_again_idempotently(self):
        close, _ = finalize_daily_close(date(2026, 7, 16), self.user, 1000)
        reopen_daily_close(close, self.user, 'تصحيح')
        close, changed = finalize_daily_close(date(2026, 7, 16), self.user, 1200)
        self.assertTrue(changed)
        self.assertEqual(close.status, DailyClose.Status.CLOSED)
        again, changed_again = finalize_daily_close(date(2026, 7, 16), self.user, 1200)
        self.assertFalse(changed_again)
        self.assertEqual(again.pk, close.pk)
        self.assertEqual(DailyClose.objects.filter(business_date=date(2026, 7, 16)).count(), 1)

    def test_purchase_draft_receive_idempotent_and_cancel_reverses_stock(self):
        purchase = Purchase.objects.create(business_date=date(2026, 7, 18), supplier_name='مورد', created_by=self.user)
        PurchaseItem.objects.create(purchase=purchase, inventory_item=self.item, quantity=Decimal('2'), unit=InventoryItem.Unit.KG, unit_cost_syp=Decimal('10'))
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, Decimal('0.000'))
        response = self.client.post(reverse('staff_purchase_detail', args=[purchase.pk]), {'action': 'receive'})
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, Decimal('2.000'))
        self.client.post(reverse('staff_purchase_detail', args=[purchase.pk]), {'action': 'receive'})
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, Decimal('2.000'))
        self.client.post(reverse('staff_purchase_detail', args=[purchase.pk]), {'action': 'cancel', 'reason': 'مرتجع'})
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, Decimal('0.000'))
        self.assertTrue(StockMovement.objects.filter(related_purchase=purchase, direction=StockMovement.Direction.OUT).exists())

    @override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
    def test_rtl_finance_pages_render(self):
        DailyClose.objects.create(business_date=date(2026, 7, 15), status=DailyClose.Status.CLOSED)
        for name in ['staff_finance_home', 'staff_daily_closes', 'staff_purchases', 'staff_finance_expenses', 'staff_finance_cash_movements']:
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'dir="rtl"')
