from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import PrepStation
from core.models import ActivityLog, Category, Order, OrderItem, Product


@override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class PrepWorkflowProfilesTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='prep-profile-admin', phone='99001', password='pass', role='admin',
        )
        self.bartender = User.objects.create_user(
            username='prep-profile-bartender', phone='99002', password='pass', role='bartender',
        )
        self.cashier = User.objects.create_user(
            username='prep-profile-cashier', phone='99003', password='pass', role='cashier',
        )
        self.bar = PrepStation.objects.create(
            code='workflow-bar', name_ar='بار التجربة',
            station_type=PrepStation.StationType.BAR,
        )
        self.kitchen = PrepStation.objects.create(
            code='workflow-kitchen', name_ar='مطبخ التجربة',
            station_type=PrepStation.StationType.KITCHEN,
        )
        self.category = Category.objects.create(name_ar='مشاريب')
        self.product = Product.objects.create(
            category=self.category,
            name_ar='شاي تجريبي',
            price_syp=100,
            prep_station_ref=self.bar,
            requires_preparation=True,
        )
        self.order = Order.objects.create()
        self.item = OrderItem.objects.create(
            order=self.order, product=self.product, prep_station=self.bar,
            product_name_ar_snapshot=self.product.name_ar, quantity=1,
            unit_price_syp_snapshot=100, line_total_syp_snapshot=100,
            prep_status=OrderItem.PrepStatus.NEW,
        )

    def status_url(self):
        return reverse('staff_kitchen_item_status', kwargs={'item_id': self.item.pk})

    def profiles_url(self):
        return reverse('staff_prep_profiles')

    def prep_audits(self):
        return list(ActivityLog.objects.filter(
            action='order_item_prep_status_changed',
        ).order_by('pk'))

    def test_legacy_stations_default_to_team_and_reject_direct_shortcuts(self):
        self.assertEqual(self.bar.workflow_profile, PrepStation.WorkflowProfile.TEAM)
        self.client.force_login(self.admin)
        board = self.client.get(reverse('staff_prep'))
        self.assertContains(board, self.product.name_ar)
        self.assertNotContains(board, 'name="action" value="serve_direct"')
        rejected = self.client.post(self.status_url(), {'action': 'serve_direct'})
        self.assertRedirects(rejected, reverse('staff_prep'), fetch_redirect_response=False)
        self.item.refresh_from_db()
        self.assertEqual(self.item.prep_status, OrderItem.PrepStatus.NEW)
        self.assertEqual(self.prep_audits(), [])

    def test_only_owner_admin_can_configure_and_all_changes_are_audited(self):
        self.client.force_login(self.bartender)
        denied = self.client.get(self.profiles_url())
        self.assertEqual(denied.status_code, 403)
        denied = self.client.post(
            self.profiles_url(),
            {'station_id': self.bar.pk, 'workflow_profile': 'direct'},
        )
        self.assertEqual(denied.status_code, 403)
        self.bar.refresh_from_db()
        self.assertEqual(self.bar.workflow_profile, 'team')

        self.client.force_login(self.admin)
        page = self.client.get(self.profiles_url())
        self.assertContains(page, 'أساليب التحضير')
        self.assertContains(page, self.bar.name_ar)
        changed = self.client.post(
            self.profiles_url(),
            {'station_id': self.bar.pk, 'workflow_profile': 'direct'},
        )
        self.assertRedirects(changed, self.profiles_url(), fetch_redirect_response=False)
        self.bar.refresh_from_db()
        self.kitchen.refresh_from_db()
        self.assertEqual(self.bar.workflow_profile, 'direct')
        self.assertEqual(self.kitchen.workflow_profile, 'team')
        events = ActivityLog.objects.filter(action='prep_station.workflow_profile_changed')
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.get().details['old_profile'], 'team')
        self.assertEqual(events.get().details['new_profile'], 'direct')

        self.client.post(
            self.profiles_url(),
            {'station_id': self.bar.pk, 'workflow_profile': 'direct'},
        )
        self.assertEqual(events.count(), 1)

    def test_invalid_profile_never_changes_station_or_logs_event(self):
        self.client.force_login(self.admin)
        result = self.client.post(
            self.profiles_url(), {'station_id': self.bar.pk, 'workflow_profile': 'invalid'},
        )
        self.assertRedirects(result, self.profiles_url(), fetch_redirect_response=False)
        self.bar.refresh_from_db()
        self.assertEqual(self.bar.workflow_profile, 'team')
        self.assertFalse(ActivityLog.objects.filter(
            action='prep_station.workflow_profile_changed',
        ).exists())

    def test_bartender_direct_mode_requires_real_serving_and_records_one_real_transition(self):
        self.bar.workflow_profile = PrepStation.WorkflowProfile.DIRECT
        self.bar.save(update_fields=['workflow_profile'])
        self.client.force_login(self.bartender)
        board = self.client.get(reverse('staff_prep_station', kwargs={'station_code': self.bar.code}))
        self.assertContains(board, self.product.name_ar)
        self.assertContains(board, 'name="action" value="serve_direct"')
        self.assertContains(board, 'تم التقديم')

        result = self.client.post(self.status_url(), {'action': 'serve_direct'})
        self.assertRedirects(result, reverse('staff_prep'), fetch_redirect_response=False)
        self.item.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.item.prep_status, OrderItem.PrepStatus.SERVED)
        self.assertEqual(self.order.status, Order.Status.NEW)  # fulfillment is separate from billing/order state
        audit = self.prep_audits()
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0].details['old_status'], 'new')
        self.assertEqual(audit[0].details['new_status'], 'served')
        self.assertEqual(audit[0].details['transition_kind'], 'direct_completion')
        self.assertEqual(audit[0].details['workflow_profile'], 'direct')

        repeat = self.client.post(self.status_url(), {'action': 'serve_direct'})
        self.assertEqual(repeat.status_code, 302)
        self.assertEqual(len(self.prep_audits()), 1)
        self.item.refresh_from_db()
        self.assertEqual(self.item.prep_status, OrderItem.PrepStatus.SERVED)

    def test_direct_mode_supports_an_existing_work_in_progress_without_fake_steps(self):
        self.bar.workflow_profile = 'direct'
        self.bar.save(update_fields=['workflow_profile'])
        self.client.force_login(self.bartender)
        # Existing valid detailed step remains possible even in Direct mode.
        accepted = self.client.post(self.status_url(), {'action': 'accept'})
        self.assertEqual(accepted.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.prep_status, OrderItem.PrepStatus.ACCEPTED)
        completed = self.client.post(self.status_url(), {'action': 'serve_direct'})
        self.assertEqual(completed.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.prep_status, OrderItem.PrepStatus.SERVED)
        audit = self.prep_audits()
        self.assertEqual(len(audit), 2)
        self.assertEqual([a.details['new_status'] for a in audit], ['accepted', 'served'])
        self.assertEqual(audit[-1].details['old_status'], 'accepted')

    def test_team_and_event_keep_the_detailed_state_machine(self):
        self.client.force_login(self.cashier)
        for profile in ('team', 'event'):
            with self.subTest(profile=profile):
                self.bar.workflow_profile = profile
                self.bar.save(update_fields=['workflow_profile'])
                self.item.prep_status = OrderItem.PrepStatus.NEW
                self.item.save(update_fields=['prep_status'])
                board = self.client.get(reverse('staff_prep'))
                self.assertNotContains(board, 'name="action" value="serve_direct"')
                for action, next_status in (
                    ('accept', OrderItem.PrepStatus.ACCEPTED),
                    ('prepare', OrderItem.PrepStatus.PREPARING),
                    ('ready', OrderItem.PrepStatus.READY),
                    ('served', OrderItem.PrepStatus.SERVED),
                ):
                    result = self.client.post(self.status_url(), {'action': action})
                    self.assertEqual(result.status_code, 302)
                    self.item.refresh_from_db()
                    self.assertEqual(self.item.prep_status, next_status)
                direct = self.client.post(self.status_url(), {'action': 'serve_direct'})
                self.assertEqual(direct.status_code, 302)
                self.item.refresh_from_db()
                self.assertEqual(self.item.prep_status, OrderItem.PrepStatus.SERVED)

    def test_direct_shortcut_disallowed_for_delivery_and_cancelled_orders(self):
        self.bar.workflow_profile = 'direct'
        self.bar.save(update_fields=['workflow_profile'])
        self.client.force_login(self.bartender)
        self.order.fulfillment_mode = Order.FulfillmentMode.DELIVERY
        self.order.save(update_fields=['fulfillment_mode'])
        denied = self.client.post(self.status_url(), {'action': 'serve_direct'})
        self.assertEqual(denied.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.prep_status, OrderItem.PrepStatus.NEW)
        self.order.fulfillment_mode = Order.FulfillmentMode.TABLE
        self.order.status = Order.Status.CANCELLED
        self.order.save(update_fields=['fulfillment_mode', 'status'])
        denied = self.client.post(self.status_url(), {'action': 'serve_direct'})
        self.assertEqual(denied.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.prep_status, OrderItem.PrepStatus.NEW)
        self.assertFalse(self.prep_audits())

    def test_one_station_mode_does_not_change_another_station_actions(self):
        self.bar.workflow_profile = 'direct'
        self.bar.save(update_fields=['workflow_profile'])
        kitchen_product = Product.objects.create(
            category=self.category, name_ar='شوربة', price_syp=100,
            prep_station_ref=self.kitchen, requires_preparation=True,
        )
        other_item = OrderItem.objects.create(
            order=self.order, product=kitchen_product, prep_station=self.kitchen,
            product_name_ar_snapshot='شوربة', unit_price_syp_snapshot=100,
            line_total_syp_snapshot=100,
        )
        self.client.force_login(self.bartender)
        result = self.client.post(
            reverse('staff_kitchen_item_status', kwargs={'item_id': other_item.pk}),
            {'action': 'serve_direct'},
        )
        self.assertEqual(result.status_code, 302)
        other_item.refresh_from_db()
        self.assertEqual(other_item.prep_status, OrderItem.PrepStatus.NEW)

    def test_non_prep_terminal_and_nonstaff_access_cannot_direct_serve(self):
        self.bar.workflow_profile = 'direct'
        self.bar.save(update_fields=['workflow_profile'])
        self.client.force_login(self.admin)
        self.item.prep_status = OrderItem.PrepStatus.NO_PREP
        self.item.save(update_fields=['prep_status'])
        denied = self.client.post(self.status_url(), {'action': 'serve_direct'})
        self.assertEqual(denied.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.prep_status, OrderItem.PrepStatus.NO_PREP)
