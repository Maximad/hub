"""Operations attention queue and compact staff navigation regressions."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Category, HubVisit, Order, OrderItem, Payment, Product, Room, TableArea


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class StaffOperationsAttentionTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username='attention-admin', phone='09280001', password='unused', role='admin')
        self.client.force_login(self.admin)
        self.room = Room.objects.create(name_ar='غرفة الاختبار')
        self.table = TableArea.objects.create(room=self.room, name_ar='طاولة 3')
        self.category = Category.objects.create(name_ar='مشروبات')
        self.product = Product.objects.create(
            category=self.category, name_ar='شاي', price_syp=200, is_available=True)

    def order(self, *, status='ready', visit=None, table=None, amount=200):
        order = Order.objects.create(status=status, visit=visit, table=table)
        OrderItem.objects.create(
            order=order, product=self.product, quantity=1,
            product_name_ar_snapshot='شاي', unit_price_syp_snapshot=amount,
            line_total_syp_snapshot=amount,
        )
        return order

    def pay(self, order, amount=200):
        return Payment.objects.create(
            order=order, amount_syp=amount, method=Payment.Method.CASH,
            created_by=self.admin)

    def test_fully_paid_ready_remains_attention_for_handoff_not_editing(self):
        visit = HubVisit.objects.create(table=self.table)
        order = self.order(visit=visit, table=self.table)
        self.pay(order)
        response = self.client.get(reverse('staff_home'))
        self.assertEqual(response.status_code, 200)
        matches = [o for o in response.context['active_orders'] if o.pk == order.pk]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].workspace_next_step, 'جاهز للتسليم')
        self.assertFalse(matches[0].workspace_can_edit_items)
        self.assertContains(response, 'مدفوع · يحتاج خدمة')
        self.assertContains(response, 'data-context-title="تفاصيل الطلب"')
        edit = self.client.get(reverse('staff_order_edit', args=[order.public_code]))
        self.assertEqual(edit.status_code, 200)
        self.assertFalse(edit.context['can_edit'])
        self.assertContains(edit, 'تفاصيل الطلب')
        self.assertNotContains(edit, '<h2 class="hub-section-title hub-page-header__title">تعديل طلب مفتوح</h2>', html=False)

    def test_partially_collected_order_is_details_only_and_item_update_blocked(self):
        order = self.order(status=Order.Status.NEW, amount=200)
        self.pay(order, amount=50)
        response = self.client.get(reverse('staff_home'))
        row = next(o for o in response.context['active_orders'] if o.pk == order.pk)
        self.assertFalse(row.workspace_can_edit_items)
        self.assertContains(response, 'data-context-title="تفاصيل الطلب"')
        item = order.items.get()
        url = reverse('staff_order_edit_update_item', args=[order.public_code, item.pk])
        blocked = self.client.post(url, {'quantity': '3', 'edit_scope': 'basic'})
        self.assertEqual(blocked.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 1)
        self.assertEqual(order.payments.count(), 1)

    def test_paid_and_served_order_is_not_live_attention(self):
        visit = HubVisit.objects.create(table=self.table)
        order = self.order(status=Order.Status.SERVED, visit=visit, table=self.table)
        self.pay(order)
        response = self.client.get(reverse('staff_home'))
        self.assertFalse(any(o.pk == order.pk for o in response.context['active_orders']))
        self.assertEqual(response.context['workspace_stats']['active_orders'], 0)
        self.assertEqual(response.context['standalone_to_collect'], [])

    def test_served_unpaid_standalone_remains_in_collection_attention(self):
        order = self.order(status=Order.Status.SERVED)
        response = self.client.get(reverse('staff_home'))
        self.assertFalse(any(o.pk == order.pk for o in response.context['active_orders']))
        self.assertIn(order.pk, [o.pk for o in response.context['standalone_to_collect']])
        self.assertContains(response, 'طلبات منفردة تم تقديمها وتنتظر القبض')
        self.assertContains(response, f'href="{reverse("staff_cashier_order", args=[order.public_code])}"')

    def test_ready_orders_prioritized_and_stat_count_not_capped(self):
        for _ in range(21):
            self.order(status=Order.Status.NEW)
        ready = self.order(status=Order.Status.READY)
        # Even if ready order is newest, serve/handoff is shown first.
        response = self.client.get(reverse('staff_home'))
        self.assertEqual(response.context['workspace_stats']['active_orders'], 22)
        self.assertEqual(response.context['workspace_stats']['ready_orders'], 1)
        self.assertTrue(response.context['workspace_stats']['more_active_orders'])
        self.assertEqual(len(response.context['active_orders']), 20)
        self.assertEqual(response.context['active_orders'][0].pk, ready.pk)
        self.assertContains(response, 'عرض باقي الطلبات (22)')

    def test_colorful_dashboard_focuses_current_day_without_losing_carryover(self):
        previous = self.order(status=Order.Status.READY)
        current = self.order(status=Order.Status.NEW)
        first = self.client.get(reverse('staff_home'))
        cutoff = first.context['workspace_business_day_start']
        Order.objects.filter(pk=previous.pk).update(created_at=cutoff - timedelta(minutes=5))
        response = self.client.get(reverse('staff_home'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['workspace_stats']['today_active_orders'], 1)
        self.assertEqual(response.context['workspace_stats']['carryover_orders'], 1)
        self.assertEqual(response.context['active_orders'][0].pk, current.pk)
        self.assertContains(response, 'id="carryover-orders"')
        self.assertContains(response, 'ops-dashboard-order-grid')
        self.assertContains(response, 'data-guest-wifi-pin', count=0)
        self.assertContains(response, 'staff_operations_designed.css')

    def test_design_keeps_role_gated_existing_actions(self):
        response = self.client.get(reverse('staff_home'))
        self.assertContains(response, 'ops-dashboard-topbar')
        self.assertContains(response, 'id="staff-more"')
        self.assertContains(response, 'ops-dashboard-filter')
        self.assertContains(response, 'href="{}"'.format(reverse('staff_kitchen')))
        self.assertContains(response, 'href="{}"'.format(reverse('staff_cashier')))

    def test_operations_shortcuts_are_collapsed_and_wifi_pin_not_a_hero(self):
        response = self.client.get(reverse('staff_home'))
        self.assertContains(response, 'class="ops-quick-actions" data-daily-start')
        self.assertContains(response, 'class="hub-daily-start__grid ops-quick-actions__links"')
        self.assertContains(response, 'id="active-orders"')
        self.assertLess(response.content.decode().index('id="active-orders"'),
                        response.content.decode().index('id="open-accounts"'))
        self.assertNotContains(response, '<section class="hub-daily-start"')
        self.assertContains(response, 'staff_operations_attention.css')

    def test_permissioned_shortcut_does_not_leak_to_kitchen(self):
        kitchen = get_user_model().objects.create_user(
            username='attention-kitchen', phone='09280002', role='kitchen', password='unused')
        self.client.force_login(kitchen)
        response = self.client.get(reverse('staff_home'))
        actions = response.context['workspace_primary_actions'] + response.context['workspace_more_actions']
        self.assertNotIn('purchase', [a['key'] for a in actions])
        self.assertNotIn('cashier', [a['key'] for a in actions])
        self.assertContains(response, 'فتح لوحة التحضير')
        self.assertNotContains(response, 'data-dialog-open="new-account-dialog"')
