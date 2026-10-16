"""Active Duty coverage is explicit and never inferred from the daily roster."""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import ActivityLog
from operations.duty import (
    accept_duty_handover, assign_duty_coverage, current_duty_users,
    eligible_duty_users, request_duty_handover,
)
from operations.models import BusinessDay, BusinessDayDutyCoverage
from operations.opening import set_staff_roster
from operations.services import open_business_day


class ActiveDutyCoverageTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username='coverage-admin', phone='+963955901111',
            email='coverage@example.org', password='pass',
        )
        self.tamer = User.objects.create_user(
            username='coverage-tamer', phone='+963955901112',
            role=User.Role.CASHIER, password='pass',
        )
        self.salwa = User.objects.create_user(
            username='coverage-salwa', phone='+963955901113',
            role=User.Role.CASHIER, password='pass',
        )
        self.waiter = User.objects.create_user(
            username='coverage-waiter', phone='+963955901114',
            role=User.Role.WAITER, password='pass',
        )
        self.outside = User.objects.create_user(
            username='coverage-outside', phone='+963955901115',
            role=User.Role.CASHIER, password='pass',
        )
        self.provider = User.objects.create_user(
            username='coverage-provider', phone='+963955901116',
            role=User.Role.INTERNET_PROVIDER, password='pass',
        )
        self.day = open_business_day(actor=self.admin)
        set_staff_roster(
            self.day, actor=self.admin,
            user_ids=[self.tamer.pk, self.salwa.pk, self.waiter.pk],
        )
        self.day.staff_assignments.update(checked_in_at=timezone.now())

    def assign(self, responsibility='service', user=None, actor=None):
        return assign_duty_coverage(
            self.day, responsibility=responsibility,
            user_id=(user or self.tamer).pk, actor=actor or self.admin,
        )

    def test_unset_coverage_is_not_all_staff_fallback(self):
        self.assertEqual(current_duty_users(self.day, 'service'), [])
        self.assertEqual(self.day.duty_coverages.count(), 0)

    def test_assignment_resolves_to_one_checked_in_authorized_staff_member(self):
        self.assign()
        self.assertEqual(current_duty_users(self.day, 'service'), [self.tamer])
        self.assertEqual(self.day.duty_coverages.count(), 1)
        self.assertTrue(ActivityLog.objects.filter(action='business_day.duty_assigned').exists())

    def test_unrostered_provider_and_non_manager_cannot_assign(self):
        with self.assertRaises(PermissionDenied):
            self.assign(actor=self.waiter)
        for person in [self.outside, self.provider]:
            with self.assertRaises(ValidationError):
                self.assign(user=person)
        self.assertEqual(self.day.duty_coverages.count(), 0)

    def test_unchecked_or_inactive_assignee_is_not_eligible(self):
        assignment = self.day.staff_assignments.get(user=self.salwa)
        assignment.checked_in_at = None
        assignment.save(update_fields=['checked_in_at'])
        with self.assertRaises(ValidationError):
            self.assign(user=self.salwa)
        self.assertNotIn(self.salwa, eligible_duty_users(self.day, 'service'))
        self.assign()
        self.tamer.is_active = False
        self.tamer.save(update_fields=['is_active'])
        self.assertEqual(current_duty_users(self.day, 'service'), [])

    def test_handover_does_not_transfer_before_recipient_accepts(self):
        self.assign()
        coverage = request_duty_handover(
            self.day, responsibility='service',
            to_user_id=self.salwa.pk, actor=self.tamer,
        )
        self.assertEqual(coverage.proposed_assignment.user, self.salwa)
        self.assertEqual(current_duty_users(self.day, 'service'), [self.tamer])
        accepted = accept_duty_handover(
            self.day, responsibility='service', actor=self.salwa,
        )
        self.assertEqual(accepted.primary_assignment.user, self.salwa)
        self.assertIsNone(accepted.proposed_assignment)
        self.assertEqual(current_duty_users(self.day, 'service'), [self.salwa])
        self.assertTrue(ActivityLog.objects.filter(
            action='business_day.duty_handover_accepted'
        ).exists())

    def test_unauthorized_and_duplicate_accept_are_rejected(self):
        self.assign()
        request_duty_handover(
            self.day, responsibility='service',
            to_user_id=self.salwa.pk, actor=self.tamer,
        )
        with self.assertRaises(PermissionDenied):
            accept_duty_handover(self.day, responsibility='service', actor=self.waiter)
        with self.assertRaises(PermissionDenied):
            request_duty_handover(
                self.day, responsibility='service',
                to_user_id=self.waiter.pk, actor=self.salwa,
            )
        accept_duty_handover(self.day, responsibility='service', actor=self.salwa)
        with self.assertRaises(ValidationError):
            accept_duty_handover(self.day, responsibility='service', actor=self.salwa)

    def test_manager_takeover_invalidates_pending_handover(self):
        self.assign()
        request_duty_handover(
            self.day, responsibility='service', to_user_id=self.salwa.pk,
            actor=self.tamer,
        )
        self.assign(user=self.waiter)
        self.assertEqual(current_duty_users(self.day, 'service'), [self.waiter])
        with self.assertRaises(ValidationError):
            accept_duty_handover(self.day, responsibility='service', actor=self.salwa)

    def test_cannot_remove_primary_or_pending_holder_from_roster(self):
        self.assign()
        request_duty_handover(
            self.day, responsibility='service', to_user_id=self.salwa.pk,
            actor=self.tamer,
        )
        for keep in ([self.salwa.pk, self.waiter.pk], [self.tamer.pk, self.waiter.pk]):
            with self.assertRaises(ValidationError):
                set_staff_roster(self.day, actor=self.admin, user_ids=keep)
        self.assertEqual(self.day.staff_assignments.count(), 3)

    def test_closing_or_closed_day_is_not_implicitly_reassigned(self):
        self.assign()
        self.day.status = BusinessDay.Status.CLOSED
        self.day.save(update_fields=['status'])
        self.assertEqual(current_duty_users(self.day, 'service'), [])
        with self.assertRaises(ValidationError):
            self.assign(user=self.salwa)

    def test_business_day_page_shows_assign_and_handover_accept_controls(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('staff_close_day'))
        self.assertContains(response, 'المسؤولون الآن')
        self.assertContains(response, 'duty_assign')
        self.client.post(reverse('staff_close_day'), {
            'business_day_action': 'duty_assign',
            'responsibility': 'service',
            'duty_user_id': str(self.tamer.pk),
        })
        self.assertEqual(current_duty_users(self.day, 'service'), [self.tamer])
        self.client.force_login(self.tamer)
        response = self.client.post(reverse('staff_close_day'), {
            'business_day_action': 'duty_handover_request',
            'responsibility': 'service',
            'duty_user_id': str(self.salwa.pk),
        })
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.salwa)
        response = self.client.get(reverse('staff_close_day'))
        self.assertContains(response, 'duty_handover_accept')
        response = self.client.post(reverse('staff_close_day'), {
            'business_day_action': 'duty_handover_accept',
            'responsibility': 'service',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(current_duty_users(self.day, 'service'), [self.salwa])

    def test_invalid_responsibility_and_id_are_non_mutating(self):
        with self.assertRaises(ValidationError):
            self.assign(responsibility='invalid')
        with self.assertRaises(ValidationError):
            assign_duty_coverage(
                self.day, responsibility='service', user_id='not-int', actor=self.admin,
            )
        self.assertFalse(BusinessDayDutyCoverage.objects.exists())
