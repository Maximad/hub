from datetime import timedelta

from django.contrib.auth import get_user_model
from accounts.models import UserCapabilityOverride
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Category,
    FinancialAccount,
    HubVisit,
    HubVisitBrowserCredential,
    InternetSession,
    Order,
    OrderItem,
    Payment,
    Product,
    Room,
    SystemSetting,
    TableArea,
)
from core.services.visit_internet_devices import start_visit_metered_session
from core.settings_helpers import get_system_settings


@override_settings(
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class StaffWorkspaceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='workspace-admin', phone='92001', password='x', role='admin'
        )
        self.waiter = User.objects.create_user(
            username='workspace-waiter', phone='92002', password='x', role='waiter'
        )
        self.kitchen = User.objects.create_user(
            username='workspace-kitchen', phone='92003', password='x', role='kitchen'
        )
        self.cashier = User.objects.create_user(
            username='workspace-cashier', phone='92004', password='x', role='cashier'
        )
        self.bartender = User.objects.create_user(
            username='workspace-bartender', phone='92005', password='x', role='bartender'
        )
        FinancialAccount.objects.create(
            code='cash:workspace-test',
            name_ar='صندوق الاختبار',
            account_type=FinancialAccount.AccountType.ASSET,
            scope='cashbox',
            is_active=True,
            negative_balance_policy=FinancialAccount.NegativeBalancePolicy.ALLOW,
        )
        FinancialAccount.objects.create(
            code='revenue:workspace-test',
            name_ar='إيراد الاختبار',
            account_type=FinancialAccount.AccountType.REVENUE,
            is_active=True,
        )
        self.room = Room.objects.create(name_ar='الصالة')
        self.table = TableArea.objects.create(room=self.room, name_ar='طاولة 7')
        self.visit = HubVisit.objects.create(table=self.table)
        self.order = Order.objects.create(
            table=self.table,
            visit=self.visit,
            status=Order.Status.READY,
            fulfillment_mode=Order.FulfillmentMode.TABLE,
            service_mode=Order.ServiceMode.TABLE,
        )

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
            allow_guest_internet_sessions=True,
            default_rate_per_hour_syp=600,
            default_minimum_minutes=30,
            default_rounding_increment_minutes=15,
            default_minimum_charge_syp=0,
            default_free_grace_minutes=0,
            auto_create_order_for_metered_sessions=True,
            internet_service_product=self.internet_product,
        )
        get_system_settings.cache_clear()

    def tearDown(self):
        get_system_settings.cache_clear()

    def _start_metered(self, visit=None):
        visit = visit or self.visit
        credential = HubVisitBrowserCredential.objects.create(
            visit=visit,
            token_hash=(f'{visit.pk:064x}')[-64:],
        )
        session, created = start_visit_metered_session(
            visit=visit,
            credential=credential,
            guest_name='زبون اختبار',
            guest_phone='0999123456',
        )
        self.assertTrue(created)
        started = timezone.now() - timedelta(minutes=31)
        InternetSession.objects.filter(pk=session.pk).update(
            start_time=started,
            started_at=started,
        )
        session.refresh_from_db()
        return session

    def test_operations_snapshot_reflects_cash_payment_on_next_read(self):
        OrderItem.objects.create(
            order=self.order, product=self.internet_product, quantity=1,
            product_name_ar_snapshot='خدمة تجريبية',
            unit_price_syp_snapshot=400, line_total_syp_snapshot=400,
        )
        self.client.force_login(self.admin)
        before = self.client.get(reverse('staff_home'))
        self.assertEqual(before.context['workspace_stats']['unpaid_orders'], 1)
        self.assertEqual(before.context['visit_rows'][0]['remaining_syp'], 400)

        collected = self.client.post(
            reverse('staff_cashier_pay', kwargs={'public_code': self.visit.public_code}),
            {'amount_syp': '400', 'method': Payment.Method.CASH},
            HTTP_IDEMPOTENCY_KEY='ops-refresh-collect',
        )
        self.assertEqual(collected.status_code, 302)

        refreshed = self.client.get(reverse('staff_home'))
        self.assertEqual(refreshed.context['workspace_stats']['unpaid_orders'], 0)
        self.assertEqual(refreshed.context['visit_rows'], [])
        self.assertContains(refreshed, 'data-operations-v2')
        self.assertNotContains(refreshed, 'data-search="' + self.visit.display_number)

    def test_operations_snapshot_removes_account_after_settle_and_close(self):
        OrderItem.objects.create(
            order=self.order, product=self.internet_product, quantity=1,
            product_name_ar_snapshot='خدمة تجريبية',
            unit_price_syp_snapshot=200, line_total_syp_snapshot=200,
        )
        self.client.force_login(self.admin)
        before = self.client.get(reverse('staff_home'))
        self.assertEqual(before.context['workspace_stats']['open_visits'], 1)

        closed = self.client.post(
            reverse('staff_cashier_pay', kwargs={'public_code': self.visit.public_code}),
            {'action': 'settle_close', 'method': Payment.Method.CASH},
            HTTP_IDEMPOTENCY_KEY='ops-refresh-close',
        )
        self.assertEqual(closed.status_code, 302)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, HubVisit.Status.CLOSED)

        refreshed = self.client.get(reverse('staff_home'))
        self.assertEqual(refreshed.context['workspace_stats']['open_visits'], 0)
        self.assertEqual(refreshed.context['workspace_stats']['unpaid_orders'], 0)
        self.assertNotContains(refreshed, 'data-search="' + self.visit.display_number)

    def test_staff_home_is_unified_front_of_house_workspace(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('staff_home'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'staff/home.html')
        self.assertContains(response, '>العمليات</h2>', html=False)
        self.assertContains(response, 'البيع والشراء والجرد والإنترنت من مساحة واحدة، بحسب صلاحيات المناوب.')
        self.assertContains(response, 'الحسابات التي تحتاج متابعة مالية')
        self.assertNotContains(response, 'data-search="' + self.visit.display_number)
        self.assertContains(response, self.order.display_number)
        self.assertContains(response, 'بيع / طلب جديد')
        self.assertContains(response, '+ فتح حساب / جلسة جديدة')
        self.assertNotContains(response, 'data-context-title="الدفع وإغلاق الحساب"')
        self.assertNotContains(response, 'data-context-title="إنترنت الحساب"')
        self.assertNotContains(response, 'وصول سريع إلى مساحات التشغيل اليومية حسب صلاحياتك.')

    def test_quick_start_is_role_aware_and_bounded(self):
        expectations = {
            'admin': ['order', 'purchase', 'count', 'internet'],
            'cashier': ['order', 'purchase', 'count', 'internet'],
            'waiter': ['order', 'tasks', 'visits', 'reservations'],
            'kitchen': ['prep', 'tasks', 'inventory'],
            'bartender': ['prep', 'tasks', 'inventory'],
        }
        for role, expected in expectations.items():
            with self.subTest(role=role):
                self.client.force_login(getattr(self, role))
                response = self.client.get(reverse('staff_home'))
                self.assertEqual(response.status_code, 200)
                primary = response.context['workspace_primary_actions']
                secondary = response.context['workspace_more_actions']
                self.assertEqual([action['key'] for action in primary], expected)
                self.assertLessEqual(len(primary), 4)
                self.assertContains(response, 'data-daily-start')
                self.assertContains(response, 'class="hub-daily-start__grid"')
                for action in primary + secondary:
                    self.assertContains(
                        response,
                        'href="{}" data-daily-action="{}"'.format(
                            action['url'], action['key'],
                        ),
                    )

    def test_cashier_can_reach_purchase_stock_and_expense_without_admin_settings(self):
        self.client.force_login(self.cashier)
        response = self.client.get(reverse('staff_home'))
        self.assertEqual(response.status_code, 200)
        keys = [action['key'] for action in response.context['workspace_primary_actions']]
        other = [action['key'] for action in response.context['workspace_more_actions']]
        self.assertIn('count', keys)
        self.assertIn('purchase', keys)
        self.assertIn('internet', keys)
        self.assertIn('cashier', other)
        self.assertContains(response, 'القبض ومتابعة الحسابات المفتوحة')
        self.assertIn('expense', other)
        self.assertNotIn('settings', keys + other)
        self.assertContains(response, 'مهام أخرى حسب صلاحياتك')

    def test_kitchen_gets_preparation_without_cashier_or_purchase_shortcuts(self):
        self.client.force_login(self.kitchen)
        response = self.client.get(reverse('staff_home'))
        keys = [a['key'] for a in (
            response.context['workspace_primary_actions'] +
            response.context['workspace_more_actions']
        )]
        self.assertEqual(keys[0], 'prep')
        self.assertNotIn('cashier', keys)
        self.assertNotIn('purchase', keys)
        self.assertNotIn('count', keys)
        self.assertNotIn('expense', keys)

    def test_stock_count_shortcut_uses_explicit_capability_and_respects_deny(self):
        UserCapabilityOverride.objects.create(
            user=self.kitchen, capability='inventory_stock_count', allowed=True,
        )
        self.client.force_login(self.kitchen)
        allowed = self.client.get(reverse('staff_home'))
        all_keys = [a['key'] for a in (
            allowed.context['workspace_primary_actions'] +
            allowed.context['workspace_more_actions']
        )]
        self.assertIn('count', all_keys)
        self.assertNotIn('purchase', all_keys)

        UserCapabilityOverride.objects.create(
            user=self.cashier, capability='inventory_stock_count', allowed=False,
        )
        self.client.force_login(self.cashier)
        denied = self.client.get(reverse('staff_home'))
        keys = [a['key'] for a in (
            denied.context['workspace_primary_actions'] +
            denied.context['workspace_more_actions']
        )]
        self.assertNotIn('count', keys)
        self.assertIn('purchase', keys)

    def test_open_account_button_requires_visit_permission_as_well_as_orders(self):
        UserCapabilityOverride.objects.create(
            user=self.waiter, capability='visit_manage', allowed=False,
        )
        self.client.force_login(self.waiter)
        response = self.client.get(reverse('staff_home'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-dialog-open="new-account-dialog"')
        self.assertNotContains(response, '<dialog id="new-account-dialog"')
        self.assertNotIn(
            'visits',
            [a['key'] for a in response.context['workspace_primary_actions']],
        )

    def test_visit_card_progressively_opens_unified_account_drawer(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('staff_home'))

        detail_url = reverse('staff_visit_detail', kwargs={'public_code': self.visit.public_code})
        self.assertContains(response, 'id="staff-context-drawer"')
        self.assertContains(response, 'data-visit-context-url="{}?panel=1"'.format(detail_url))
        self.assertContains(response, 'js/staff_workspace.js')

    def test_visit_panel_contains_order_payment_and_internet_actions(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('staff_visit_detail', kwargs={'public_code': self.visit.public_code}),
            {'panel': '1'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'staff/_visit_panel.html')
        self.assertContains(response, 'حساب الزبون')
        self.assertContains(response, self.table.name_ar)
        self.assertContains(response, '+ إضافة طلب')
        self.assertContains(response, 'الدفع')
        self.assertContains(response, 'الإنترنت')
        self.assertContains(response, '?panel=internet')
        self.assertContains(response, self.order.display_number)
        self.assertNotContains(response, '<!DOCTYPE html>')

    def test_context_bound_pos_preselects_requested_open_visit(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('staff_pos'),
            {'visit': str(self.visit.public_code)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_visit_id'], str(self.visit.pk))
        self.assertContains(response, 'value="{}" selected'.format(self.visit.pk))

    def test_staff_primary_navigation_is_reduced_to_operations_and_specialists(self):
        self.client.force_login(self.waiter)
        response = self.client.get(reverse('staff_orders'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'التنقل التشغيلي')
        self.assertContains(response, '>العمليات</a>', html=False)
        self.assertContains(response, '>الأقسام</a>', html=False)
        self.assertNotContains(response, '>الجلسات</a>', html=False)
        self.assertNotContains(response, '>الدفع</a>', html=False)
        self.assertNotContains(response, '>الإنترنت</a>', html=False)
        self.assertNotContains(response, '>المالية</a>', html=False)
        self.assertNotContains(response, '>التقارير</a>', html=False)

    def test_internet_tab_is_visible_to_staff_with_internet_capability(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('staff_home'))
        internet_url = reverse('staff_internet')

        self.assertContains(response, f'href="{internet_url}">الإنترنت</a>')
        self.assertContains(response, f'href="{internet_url}">الإنترنت والجلسات</a>')
        self.assertEqual(self.client.get(internet_url).status_code, 200)

        self.client.force_login(self.waiter)
        waiter_home = self.client.get(reverse('staff_home'))
        self.assertNotContains(waiter_home, f'href="{internet_url}">الإنترنت</a>')
        self.assertNotContains(waiter_home, f'href="{internet_url}">الإنترنت والجلسات</a>')

    def test_kitchen_workspace_keeps_financial_actions_hidden_but_exposes_operations_controls(self):
        self.client.force_login(self.kitchen)
        response = self.client.get(reverse('staff_home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'فتح لوحة التحضير')
        self.assertNotContains(response, '+ طلب جديد')
        self.assertNotContains(response, '+ فتح حساب / جلسة جديدة')
        self.assertNotContains(response, 'data-context-title="الدفع وإغلاق الحساب"')
        self.assertContains(response, 'كل الطلبات')
        self.assertContains(response, 'كل الجلسات')
        self.assertNotContains(response, 'data-context-title="إنترنت الحساب"')
        self.assertContains(response, '>تعديل</a>', html=False)

    def test_all_operational_staff_can_open_order_edit_and_visit_management(self):
        for user in (self.admin, self.cashier, self.waiter, self.kitchen, self.bartender):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                orders = self.client.get(reverse('staff_orders'))
                self.assertEqual(orders.status_code, 200)
                self.assertContains(
                    orders,
                    reverse('staff_order_edit', kwargs={'public_code': self.order.public_code}),
                )
                edit = self.client.get(
                    reverse('staff_order_edit', kwargs={'public_code': self.order.public_code}),
                )
                self.assertEqual(edit.status_code, 200)
                visit = self.client.get(
                    reverse('staff_visit_detail', kwargs={'public_code': self.visit.public_code}),
                )
                self.assertEqual(visit.status_code, 200)
                self.assertContains(visit, 'إدارة الجلسة')

    def test_operational_staff_can_cancel_order_without_hard_deleting_audit_record(self):
        self.client.force_login(self.kitchen)
        response = self.client.post(
            reverse('staff_order_status', kwargs={'public_code': self.order.public_code}),
            {
                'status': Order.Status.CANCELLED,
                'cancellation_reason': 'staff_mistake',
                'cancellation_notes': 'اختبار',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELLED)
        self.assertEqual(self.order.cancellation_reason, 'staff_mistake')
        self.assertTrue(Order.objects.filter(pk=self.order.pk).exists())

    def test_operational_staff_can_delete_only_empty_visit(self):
        empty_visit = HubVisit.objects.create(table=self.table)
        self.client.force_login(self.bartender)

        deleted = self.client.post(
            reverse('staff_visit_detail', kwargs={'public_code': empty_visit.public_code}),
            {'action': 'delete_empty'},
        )
        self.assertEqual(deleted.status_code, 302)
        self.assertFalse(HubVisit.objects.filter(pk=empty_visit.pk).exists())

        blocked = self.client.post(
            reverse('staff_visit_detail', kwargs={'public_code': self.visit.public_code}),
            {'action': 'delete_empty'},
        )
        self.assertEqual(blocked.status_code, 302)
        self.assertTrue(HubVisit.objects.filter(pk=self.visit.pk).exists())

    def test_operational_staff_can_stop_internet_inside_visit_without_billing_access(self):
        session = self._start_metered()
        self.client.force_login(self.kitchen)
        panel_url = reverse(
            'staff_visit_detail',
            kwargs={'public_code': self.visit.public_code},
        )

        panel = self.client.get(panel_url, {'panel': 'internet'})
        self.assertEqual(panel.status_code, 200)
        self.assertContains(panel, 'إيقاف هذه الجلسة')

        stopped = self.client.post(
            panel_url + '?panel=internet',
            {'action': 'internet_stop', 'session_id': session.pk},
        )
        self.assertEqual(stopped.status_code, 200)
        session.refresh_from_db()
        self.assertNotEqual(session.status, InternetSession.Status.ACTIVE)

    def test_staff_can_open_second_independent_account_on_same_table_from_operations(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('staff_visits'),
            {'table': self.table.pk, 'notes': 'حساب منفصل', 'next': 'workspace'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith('/staff/?visit='))
        self.assertEqual(
            HubVisit.objects.filter(table=self.table, status=HubVisit.Status.OPEN).count(),
            2,
        )

    def test_internet_panel_and_stop_action_are_scoped_to_selected_account(self):
        session = self._start_metered()
        other_visit = HubVisit.objects.create(table=self.table)
        other_session = self._start_metered(other_visit)
        self.client.force_login(self.admin)

        panel_url = reverse('staff_visit_detail', kwargs={'public_code': self.visit.public_code})
        panel = self.client.get(panel_url, {'panel': 'internet'})
        self.assertEqual(panel.status_code, 200)
        self.assertTemplateUsed(panel, 'staff/_visit_internet_panel.html')
        self.assertContains(panel, 'إنترنت الحساب')
        self.assertContains(panel, f'جلسة إنترنت #{session.pk}')
        self.assertNotContains(panel, f'جلسة إنترنت #{other_session.pk}')

        wrong = self.client.post(
            panel_url + '?panel=internet',
            {'action': 'internet_stop', 'session_id': other_session.pk},
        )
        self.assertEqual(wrong.status_code, 404)
        other_session.refresh_from_db()
        self.assertEqual(other_session.status, InternetSession.Status.ACTIVE)

        stopped = self.client.post(
            panel_url + '?panel=internet',
            {'action': 'internet_stop', 'session_id': session.pk},
        )
        self.assertEqual(stopped.status_code, 200)
        session.refresh_from_db()
        self.assertNotEqual(session.status, InternetSession.Status.ACTIVE)
        self.assertContains(stopped, 'تم إيقاف جلسة الإنترنت وتحديث الحساب')

    def test_payment_drawer_can_settle_final_internet_charge_and_close_visit(self):
        session = self._start_metered()
        self.client.force_login(self.admin)
        pay_url = reverse('staff_cashier_pay', kwargs={'public_code': self.visit.public_code})
        panel_url = reverse('staff_cashier_order', kwargs={'public_code': self.visit.public_code})

        panel = self.client.get(panel_url, {'panel': 'payment'})
        self.assertEqual(panel.status_code, 200)
        self.assertContains(panel, 'تسديد المتبقي وإغلاق الحساب')
        self.assertContains(panel, 'name="method"')
        self.assertContains(panel, 'جلسة إنترنت فعالة')

        settled = self.client.post(
            pay_url + '?panel=payment',
            {
                'action': 'settle_close',
                'method': 'cash',
                'notes': 'إغلاق من العمليات',
            },
        )
        self.assertEqual(settled.status_code, 200)
        self.visit.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(self.visit.status, HubVisit.Status.CLOSED)
        self.assertNotEqual(session.status, InternetSession.Status.ACTIVE)
        self.assertEqual(self.visit.remaining_syp, 0)
        self.assertContains(settled, 'تم تسديد كامل حساب الجلسة وإغلاقها')
