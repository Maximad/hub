from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.forms import StaffUserCreateForm, StaffUserEditForm
from accounts.middleware import VIEW_AS_USER_SESSION_KEY
from accounts.models import AccessProfile, UserCapabilityOverride
from accounts.permissions import get_staff_capabilities, user_has_capability


class StaffCapabilityTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.service_profile = AccessProfile.objects.get(code='service-operations')
        self.prep_profile = AccessProfile.objects.get(code='prep-operations')
        self.provider_profile = AccessProfile.objects.get(code='internet-provider')
        self.general_profile = AccessProfile.objects.get(code='general-staff')
        self.admin = User.objects.create_user(
            username='cap-admin', password='pass', phone='+963100000001', role='admin'
        )
        self.waiter = User.objects.create_user(
            username='cap-waiter', password='pass', phone='+963100000002', role='waiter'
        )
        self.kitchen = User.objects.create_user(
            username='cap-kitchen', password='pass', phone='+963100000003', role='kitchen'
        )

    def test_role_defaults_keep_kitchen_board_to_admin_and_kitchen(self):
        self.assertTrue(user_has_capability(self.admin, 'kitchen_board'))
        self.assertTrue(user_has_capability(self.kitchen, 'kitchen_board'))
        self.assertFalse(user_has_capability(self.waiter, 'kitchen_board'))

    def test_per_user_allow_override_grants_capability(self):
        UserCapabilityOverride.objects.create(
            user=self.waiter,
            capability='kitchen_board',
            allowed=True,
        )
        self.assertTrue(user_has_capability(self.waiter, 'kitchen_board'))

    def test_per_user_deny_override_removes_role_capability(self):
        UserCapabilityOverride.objects.create(
            user=self.kitchen,
            capability='inventory',
            allowed=False,
        )
        self.assertFalse(user_has_capability(self.kitchen, 'inventory'))
        self.assertTrue(user_has_capability(self.kitchen, 'kitchen_board'))

    def test_superuser_remains_emergency_all_access(self):
        self.admin.is_superuser = True
        self.admin.is_staff = True
        self.admin.save(update_fields=('is_superuser', 'is_staff'))
        UserCapabilityOverride.objects.create(
            user=self.admin,
            capability='finance',
            allowed=False,
        )
        self.assertTrue(user_has_capability(self.admin, 'finance'))

    def test_inactive_user_has_no_effective_capabilities(self):
        self.waiter.is_active = False
        self.waiter.save(update_fields=('is_active',))
        self.assertFalse(any(get_staff_capabilities(self.waiter).values()))

    def test_edit_form_persists_allow_and_deny_overrides(self):
        form = StaffUserEditForm(
            data={
                'first_name': self.waiter.first_name,
                'last_name': self.waiter.last_name,
                'email': self.waiter.email,
                'phone': self.waiter.phone,
                'job_title': 'منسق خدمة',
                'access_profile': self.service_profile.pk,
                'is_active': 'on',
                'capability_allow': ['kitchen_board'],
                'capability_deny': ['reservations'],
            },
            instance=self.waiter,
            actor=self.admin,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertTrue(user_has_capability(self.waiter, 'kitchen_board'))
        self.assertFalse(user_has_capability(self.waiter, 'reservations'))

    def test_access_profile_is_authoritative_over_legacy_role_defaults(self):
        photographer = get_user_model().objects.create_user(
            username='photo-user',
            password='pass',
            phone='+963100000099',
            role='waiter',
            job_title='مصور',
            access_profile=self.general_profile,
        )
        self.assertTrue(user_has_capability(photographer, 'staff_home'))
        self.assertFalse(user_has_capability(photographer, 'orders'))
        self.assertFalse(user_has_capability(photographer, 'pos'))

    def test_custom_profile_can_fit_job_title_without_creating_new_role(self):
        profile = AccessProfile.objects.create(
            code='event-team-test',
            name_ar='إدارة فعاليات',
            base_role=AccessProfile.BaseRole.STAFF,
            capabilities=['staff_home', 'events', 'reservations'],
        )
        user = get_user_model().objects.create_user(
            username='event-manager',
            password='pass',
            phone='+963100000098',
            role='staff',
            job_title='مدير فعاليات',
            access_profile=profile,
        )
        self.assertEqual(user.job_title, 'مدير فعاليات')
        self.assertTrue(user_has_capability(user, 'events'))
        self.assertTrue(user_has_capability(user, 'reservations'))
        self.assertFalse(user_has_capability(user, 'cashier'))

    def test_profile_changes_apply_to_all_assigned_users_without_job_title_changes(self):
        user = get_user_model().objects.create_user(
            username='profile-live',
            password='pass',
            phone='+963100000097',
            role='staff',
            job_title='مسؤول صوت',
            access_profile=self.general_profile,
        )
        self.assertFalse(user_has_capability(user, 'events'))
        self.general_profile.capabilities = ['staff_home', 'events']
        self.general_profile.save(update_fields=['capabilities', 'updated_at'])
        user.refresh_from_db()
        self.assertTrue(user_has_capability(user, 'events'))
        self.assertEqual(user.job_title, 'مسؤول صوت')

    def test_internet_provider_role_is_isolated_from_staff_and_has_provider_capabilities(self):
        provider = get_user_model().objects.create_user(
            username='cap-provider', password='pass', phone='+963100000004',
            role='internet_provider',
        )
        self.assertTrue(user_has_capability(provider, 'provider_dashboard'))
        self.assertTrue(user_has_capability(provider, 'internet_view_members'))
        self.assertTrue(user_has_capability(provider, 'internet_manage_network'))
        self.assertFalse(user_has_capability(provider, 'staff_home'))
        self.assertFalse(user_has_capability(provider, 'finance'))
        self.assertFalse(user_has_capability(provider, 'users'))

    def test_create_form_requires_and_syncs_provider_association_without_admin_access(self):
        from core.models import InternetPartner, InternetPartnerUser
        partner = InternetPartner.objects.create(name='Form ISP', active=True)
        form = StaffUserCreateForm(
            data={
                'username': 'form-provider',
                'first_name': 'Provider',
                'last_name': '',
                'email': '',
                'phone': '+963100000005',
                'job_title': 'مسؤول مزوّد',
                'access_profile': self.provider_profile.pk,
                'is_active': 'on',
                'allow_django_admin_access': 'on',
                'internet_partner': partner.pk,
                'can_view_customer_phone': 'on',
                'password': 'safe-test-password-8841',
                'confirm_password': 'safe-test-password-8841',
            },
            actor=self.admin,
        )
        self.assertTrue(form.is_valid(), form.errors)
        provider = form.save()
        association = InternetPartnerUser.objects.get(user=provider)
        self.assertEqual(association.partner, partner)
        self.assertTrue(association.can_view_customer_phone)
        self.assertFalse(provider.is_staff)
        self.assertFalse(provider.is_superuser)


class ViewAsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username='view-super',
            password='pass',
            email='view-super@example.com',
            phone='+963100000201',
            access_profile=AccessProfile.objects.get(code='full-admin'),
            role='admin',
        )
        self.target = User.objects.create_user(
            username='view-target',
            password='pass',
            phone='+963100000202',
            first_name='سلوى',
            job_title='مديرة فعاليات',
            access_profile=AccessProfile.objects.create(
                code='view-events',
                name_ar='فعاليات فقط',
                base_role=AccessProfile.BaseRole.STAFF,
                capabilities=['staff_home', 'events'],
            ),
            role='staff',
        )
        self.client.force_login(self.superuser)

    def test_superuser_can_enter_read_only_view_as(self):
        response = self.client.get(
            reverse('staff_user_view_as', kwargs={'user_id': self.target.pk}),
        )
        self.assertRedirects(response, reverse('staff_home'), fetch_redirect_response=False)
        self.assertEqual(
            self.client.session.get(VIEW_AS_USER_SESSION_KEY),
            self.target.pk,
        )

        preview = self.client.get(reverse('staff_home'))
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.wsgi_request.user.pk, self.target.pk)
        self.assertContains(preview, 'View As')
        self.assertContains(preview, 'سلوى')
        self.assertContains(preview, 'مديرة فعاليات')
        self.assertTrue(preview.context['staff_caps']['events'])
        self.assertFalse(preview.context['staff_caps']['pos'])

    def test_view_as_blocks_mutations(self):
        self.client.get(
            reverse('staff_user_view_as', kwargs={'user_id': self.target.pk}),
        )
        response = self.client.post(reverse('staff_pos'), {})
        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response,
            'وضع View As للمعاينة فقط',
            status_code=403,
        )

    def test_exit_view_as_restores_real_superuser_session(self):
        self.client.get(
            reverse('staff_user_view_as', kwargs={'user_id': self.target.pk}),
        )
        response = self.client.get(reverse('staff_view_as_exit'))
        self.assertRedirects(
            response,
            reverse('staff_user_detail', kwargs={'user_id': self.target.pk}),
            fetch_redirect_response=False,
        )
        self.assertNotIn(VIEW_AS_USER_SESSION_KEY, self.client.session)

        page = self.client.get(reverse('staff_home'))
        self.assertEqual(page.wsgi_request.user.pk, self.superuser.pk)

    def test_non_superuser_cannot_start_view_as(self):
        self.client.force_login(self.target)
        response = self.client.get(
            reverse('staff_user_view_as', kwargs={'user_id': self.superuser.pk}),
        )
        self.assertEqual(response.status_code, 403)
