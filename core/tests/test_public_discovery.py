from dataclasses import asdict
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalog.models import MediaAsset
from core.services.public_discovery import (
    membership_item, popup_item, program_item, public_discovery_context,
    public_membership_candidates, public_popups, public_programs,
)
from core.settings_helpers import get_system_settings
from members.models import MembershipBenefitRule, MembershipPlan, Program
from vendors.models import Vendor, VendorMedia, VendorParticipation


@override_settings(PUBLIC_HOMEPAGE_ENABLED=True, ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class PublicDiscoveryTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        clock = patch('django.utils.timezone.now', return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)
        get_system_settings.cache_clear()
        self.addCleanup(get_system_settings.cache_clear)

    def program(self, code='open-program', **kwargs):
        return Program.objects.create(**dict(
            dict(code=code, name_ar='برنامج معلن', is_active=True, is_public=True,
                 status=Program.Status.OPEN, starts_at=self.now+timedelta(days=1)), **kwargs))

    def vendor(self, **kwargs):
        return Vendor.objects.create(**dict(dict(name_ar='ضيف معلن', is_active=True, is_public=True), **kwargs))

    def popup(self, **kwargs):
        vendor = kwargs.pop('vendor', None) or self.vendor()
        return VendorParticipation.objects.create(**dict(dict(
            vendor=vendor, title_ar='تجربة معلنة', status=VendorParticipation.Status.ACTIVE,
            is_public=True, starts_at=self.now+timedelta(days=1)), **kwargs))

    def plan(self, code='public-plan', **kwargs):
        return MembershipPlan.objects.create(**dict(dict(
            code=code, name_ar='خطة معلنة', is_active=True, visible_to_customer=True,
            price_syp=14500, billing_period=MembershipPlan.BillingPeriod.MONTHLY), **kwargs))

    def test_program_gate_dates_status_and_stable_ordering(self):
        first = self.program('first', starts_at=self.now-timedelta(days=1), ends_at=self.now+timedelta(hours=1))
        second = self.program('second', starts_at=self.now)
        third = self.program('third', starts_at=self.now)
        undated = self.program('undated', starts_at=None)
        invalid = [self.program('private', is_public=False), self.program('inactive', is_active=False),
                   self.program('ended', ends_at=self.now)]
        invalid += [self.program(s, status=s) for s in ['draft', 'completed', 'cancelled']]
        self.assertEqual(list(public_programs(self.now)), [first, second, third, undated])
        for program in invalid:
            self.assertEqual(self.client.get(f'/programs/{program.code}/').status_code, 404)
        self.assertEqual(self.client.get('/programs/missing/').status_code, 404)
        self.assertContains(self.client.get('/programs/undated/'), 'تُعلن لاحقاً')

    def test_defaults_are_private_for_all_new_publication_flags(self):
        program = Program.objects.create(code='internal', name_ar='internal', status='open')
        vendor = Vendor.objects.create(name_ar='internal')
        popup = VendorParticipation.objects.create(vendor=vendor, title_ar='internal', starts_at=self.now, status='active')
        asset = MediaAsset.objects.create(title_ar='asset', external_url='https://example.com/test.png')
        media = VendorMedia.objects.create(vendor=vendor, media_asset=asset)
        self.assertFalse(any(obj.is_public for obj in [program, vendor, popup, media]))
        self.assertEqual(list(public_programs(self.now)), [])
        self.assertEqual(list(public_popups(self.now)), [])

    def test_popup_requires_both_public_flags_and_active_vendor(self):
        popup = self.popup()
        self.assertEqual(self.client.get(f'/popups/{popup.uuid}/').status_code, 200)
        for field, value in [('is_public', False), ('is_active', False)]:
            Vendor.objects.filter(pk=popup.vendor_id).update(**{field: value})
            self.assertEqual(list(public_popups(self.now)), [])
            self.assertEqual(self.client.get(f'/popups/{popup.uuid}/').status_code, 404)
            Vendor.objects.filter(pk=popup.vendor_id).update(**{field: True})
        popup.is_public = False
        popup.save()
        self.assertEqual(list(public_popups(self.now)), [])
        self.assertEqual(self.client.get(f'/popups/{uuid4()}/').status_code, 404)

    def test_popup_no_planned_advertising_and_no_end_boundary(self):
        ongoing = self.popup(starts_at=self.now-timedelta(hours=1), ends_at=self.now+timedelta(seconds=1))
        at_start = self.popup(starts_at=self.now)
        future = self.popup()
        hidden = [self.popup(status=s) for s in ['planned', 'completed', 'cancelled']]
        hidden += [self.popup(starts_at=self.now-timedelta(seconds=1)),
                   self.popup(starts_at=self.now-timedelta(hours=1), ends_at=self.now)]
        self.assertEqual(list(public_popups(self.now)), [ongoing, at_start, future])
        for popup in hidden:
            self.assertEqual(self.client.get(f'/popups/{popup.uuid}/').status_code, 404)

    def test_vendor_internal_fields_and_links_never_enter_view_model_or_html(self):
        vendor = self.vendor(contact_person='PRIVATE_CONTACT', phone='PRIVATE_PHONE',
                             settlement_notes='PRIVATE_SETTLEMENT', commission_fixed_syp=98231)
        popup = self.popup(vendor=vendor, notes='PRIVATE_NOTES')
        response = self.client.get(f'/popups/{popup.uuid}/')
        model = popup_item(public_popups(self.now).get(pk=popup.pk))
        self.assertEqual(model.name, vendor.name_ar)
        for secret in ['PRIVATE_CONTACT', 'PRIVATE_PHONE', 'PRIVATE_SETTLEMENT', 'PRIVATE_NOTES', '98231']:
            self.assertNotContains(response, secret)
            self.assertNotIn(secret, str(asdict(model)))
        vendor.name_ar = ''
        vendor.save()
        self.assertNotContains(self.client.get(f'/popups/{popup.uuid}/'), 'PRIVATE_PHONE')

    def test_only_explicitly_public_active_vendor_artwork(self):
        popup = self.popup()
        private = MediaAsset.objects.create(title_ar='private', external_url='https://example.com/private.png')
        VendorMedia.objects.create(vendor=popup.vendor, media_asset=private, role='cover', is_primary=True)
        public = MediaAsset.objects.create(title_ar='public', external_url='https://example.com/public.png', alt_text_ar='صورة الضيف')
        media = VendorMedia.objects.create(vendor=popup.vendor, media_asset=public, role='cover', is_public=True)
        response = self.client.get(f'/popups/{popup.uuid}/')
        self.assertContains(response, public.safe_url)
        self.assertNotContains(response, private.safe_url)
        for obj, field in [(media, 'is_active'), (media, 'is_public'), (public, 'is_active')]:
            setattr(obj, field, False)
            obj.save()
            self.assertNotContains(self.client.get(f'/popups/{popup.uuid}/'), public.external_url)
            setattr(obj, field, True)
            obj.save()
        public.external_url = 'javascript:alert(1)'
        public.save()
        self.assertNotContains(self.client.get(f'/popups/{popup.uuid}/'), 'javascript:')
        public.external_url = ''
        public.file = 'missing/vendor.png'
        public.save()
        self.assertNotContains(self.client.get(f'/popups/{popup.uuid}/'), 'missing/vendor.png')
        public.delete()
        self.assertEqual(self.client.get(f'/popups/{popup.uuid}/').status_code, 200)

    def test_memberships_visibility_price_and_term_without_private_metadata(self):
        plan = self.plan(notes='PRIVATE_PLAN_NOTES', metadata={'internal': 'PRIVATE_METADATA'},
                         description_ar='وصف معتمد')
        MembershipBenefitRule.objects.create(plan=plan, benefit_type='booking_priority',
                                            notes='PRIVATE_BENEFIT', metadata={'secret': 'PRIVATE_RULE'})
        response = self.client.get('/memberships/public-plan/')
        for value in ['14500', 'ل.س', 'شهر واحد', 'وصف معتمد']:
            self.assertContains(response, value)
        for secret in ['PRIVATE_PLAN_NOTES', 'PRIVATE_METADATA', 'PRIVATE_BENEFIT', 'PRIVATE_RULE', 'أولوية الحجز', 'اشترك الآن']:
            self.assertNotContains(response, secret)
            self.assertNotIn(secret, str(asdict(membership_item(public_membership_candidates().get(pk=plan.pk)))))
        for field in ['is_active', 'visible_to_customer']:
            MembershipPlan.objects.filter(pk=plan.pk).update(**{field: False})
            self.assertEqual(self.client.get('/memberships/public-plan/').status_code, 404)
            MembershipPlan.objects.filter(pk=plan.pk).update(**{field: True})
        self.assertEqual(self.client.get('/memberships/missing/').status_code, 404)

    def test_membership_known_readiness_error_hides_every_surface(self):
        plan = self.plan()
        MembershipBenefitRule.objects.create(plan=plan, benefit_type='internet_minutes', included_minutes=0)
        self.assertEqual(self.client.get('/memberships/public-plan/').status_code, 404)
        self.assertNotContains(self.client.get('/memberships/'), plan.name_ar)
        self.assertNotContains(self.client.get('/'), plan.name_ar)
        # Fixing the authoritative configuration makes it browseable, without
        # sale, entitlement or payment writes from a public request.
        plan.benefit_rules.all().delete()
        self.assertContains(self.client.get('/memberships/public-plan/'), plan.name_ar)

    def test_zero_price_is_not_lost_to_truthiness(self):
        self.plan(price_syp=0)
        self.assertContains(self.client.get('/memberships/public-plan/'), '0 ل.س')

    def test_public_pages_escape_markup_and_format_dates_in_damascus(self):
        program = self.program(name_ar='<script>name</script>', description_ar='<img src=x onerror=alert(1)>')
        with timezone.override('America/New_York'):
            response = self.client.get(f'/programs/{program.code}/')
        self.assertContains(response, '&lt;script&gt;')
        self.assertContains(response, '&lt;img')
        self.assertNotContains(response, '<script>name</script>')
        self.assertContains(response, '+03:00')
        self.assertContains(response, 'دمشق')
        self.assertContains(response, 'dir="rtl"')
        self.assertContains(response, '<main ', count=1)
        self.assertContains(response, '<h1>', count=1)

    def test_public_reads_do_not_mutate_database(self):
        program, popup, plan = self.program(), self.popup(), self.plan()
        paths = ['/', '/programs/', f'/programs/{program.code}/', '/popups/', f'/popups/{popup.uuid}/',
                 '/memberships/', f'/memberships/{plan.code}/']
        with CaptureQueriesContext(connection) as queries:
            for path in paths:
                self.assertEqual(self.client.get(path).status_code, 200)
        for query in queries:
            self.assertFalse(query['sql'].lstrip().split()[0].upper() in ['INSERT', 'UPDATE', 'DELETE'], query['sql'])
        for path in paths[1:]:
            self.assertEqual(self.client.head(path).status_code, 200)
            for method in ['post', 'put', 'patch', 'delete']:
                self.assertEqual(getattr(self.client, method)(path).status_code, 405)

    def test_withdrawal_and_status_changes_revalidate_canonical_urls(self):
        program, popup, plan = self.program(), self.popup(), self.plan()
        for obj, field, path in [(program, 'is_public', f'/programs/{program.code}/'),
                                 (popup, 'is_public', f'/popups/{popup.uuid}/'),
                                 (plan, 'visible_to_customer', f'/memberships/{plan.code}/')]:
            self.assertContains(self.client.get('/'), obj.name_ar if hasattr(obj, 'name_ar') else obj.title_ar)
            setattr(obj, field, False)
            obj.save()
            response = self.client.get(path)
            self.assertEqual(response.status_code, 404)
            self.assertIn('no-store', self.client.get('/')['Cache-Control'])

    def test_homepage_limits_and_bounded_queries(self):
        for index in range(5):
            self.program(str(index))
            self.popup()
            self.plan(str(index), sort_order=index)
        with self.assertNumQueries(5):
            context = public_discovery_context(now=self.now)
        self.assertEqual({k: len(v) for k, v in context.items()}, {'programs': 3, 'popups': 3, 'memberships': 3})

    def test_pagination_and_membership_readiness_work_are_bounded(self):
        for index in range(14):
            self.program(str(index))
            self.popup()
            self.plan(str(index), sort_order=index)
        for path in ['/programs/', '/popups/', '/memberships/']:
            self.assertEqual(len(self.client.get(path).context['items']), 12)
            self.assertEqual(len(self.client.get(path+'?page=2').context['items']), 2)
        with patch('core.services.public_discovery.assess_membership_plan', return_value={'can_sell': True}) as assessor:
            self.client.get('/memberships/')
            self.assertEqual(assessor.call_count, 12)

    @override_settings(PUBLIC_HOMEPAGE_ENABLED=False)
    def test_release_gate_hides_all_discovery_routes(self):
        program, popup, plan = self.program(), self.popup(), self.plan()
        for path in ['/programs/', f'/programs/{program.code}/', '/popups/', f'/popups/{popup.uuid}/',
                     '/memberships/', f'/memberships/{plan.code}/']:
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertTemplateUsed(self.client.get('/'), 'core/dashboard.html')

    def test_superuser_publication_uses_existing_admin_audit(self):
        from django.contrib.admin.models import LogEntry
        program = self.program(is_public=False)
        user = get_user_model().objects.create_superuser(username='public-editor', password='test', email='editor@example.test')
        self.client.force_login(user)
        response = self.client.post(f'/admin/members/program/{program.pk}/change/', {
            'code': program.code, 'name_ar': program.name_ar, 'status': 'open',
            'is_active': 'on', 'is_public': 'on', 'metadata': '{}', '_save': 'Save',
        })
        self.assertEqual(response.status_code, 302)
        program.refresh_from_db()
        self.assertTrue(program.is_public)
        self.assertTrue(LogEntry.objects.filter(user=user, object_id=str(program.pk), action_flag=2).exists())

    def test_non_superuser_cannot_publish_through_admin(self):
        user = get_user_model().objects.create_user(username='not-publisher', role='admin', is_staff=True)
        self.client.force_login(user)
        for app, model in [('members', 'program'), ('vendors', 'vendor'), ('vendors', 'vendorparticipation'), ('vendors', 'vendormedia')]:
            response = self.client.post(f'/admin/{app}/{model}/add/', {'is_public': True})
            self.assertEqual(response.status_code, 302)
            self.assertIn('/admin/login/', response.url)


class PublicationMigrationTests(TransactionTestCase):
    def test_existing_records_remain_private_after_migration(self):
        before = [('members', '0009_commercialallocation_commercial_allocation_amount_nonnegative_and_more'),
                  ('vendors', '0003_vendormedia')]
        executor = MigrationExecutor(connection)
        leaves = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate(before)
            apps = executor.loader.project_state(before).apps
            old_program = apps.get_model('members', 'Program').objects.create(code='legacy-program', name_ar='Legacy', status='open')
            old_vendor = apps.get_model('vendors', 'Vendor').objects.create(name_ar='Legacy')
            old_popup = apps.get_model('vendors', 'VendorParticipation').objects.create(
                vendor_id=old_vendor.pk, title_ar='Legacy', starts_at=timezone.now(), status='active')
            asset = apps.get_model('catalog', 'MediaAsset').objects.create(title_ar='legacy', external_url='https://example.com/legacy.png')
            old_media = apps.get_model('vendors', 'VendorMedia').objects.create(vendor_id=old_vendor.pk, media_asset_id=asset.pk)
            executor = MigrationExecutor(connection)
            executor.migrate(leaves)
            for model, pk in [(Program, old_program.pk), (Vendor, old_vendor.pk),
                              (VendorParticipation, old_popup.pk), (VendorMedia, old_media.pk)]:
                self.assertFalse(model.objects.get(pk=pk).is_public)
        finally:
            MigrationExecutor(connection).migrate(leaves)
