from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from core.models import Member
from members.models import MembershipPlan
from vendors.models import Vendor


class StaffFormValidationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='staff1',
            password='pass1234',
            phone='0999000001',
            is_staff=True,
        )
        self.client.force_login(self.user)

    def test_member_subscribe_invalid_numeric_renders_error(self):
        member = Member.objects.create(name_ar='عضو', phone='0999000002')
        plan = MembershipPlan.objects.create(code='basic', name_ar='أساسي', is_active=True)

        response = self.client.post(
            reverse('staff_member_subscribe', kwargs={'member_id': member.public_code}),
            {
                'plan': str(plan.id),
                'starts_at': '2026-05-27T10:00',
                'remaining_internet_minutes': '-5',
                'remaining_credit_syp': '10',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'الدقائق المتبقية يجب أن يكون 0 أو أكبر.')

    def test_event_new_invalid_datetime_renders_error(self):
        response = self.client.post(
            reverse('staff_event_new'),
            {
                'title_ar': 'فعالية',
                'starts_at': 'not-a-date',
                'capacity': '10',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'صيغة وقت البداية غير صحيحة.')

    def test_reservation_new_invalid_numeric_renders_error(self):
        response = self.client.post(
            reverse('staff_reservation_new'),
            {
                'name': 'حجز',
                'phone': '0999000003',
                'reservation_date': '2026-05-30',
                'start_time': '10:00',
                'party_size': 'abc',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'عدد الأشخاص يجب أن يكون رقماً صحيحاً.')

    def test_vendor_participation_invalid_datetime_renders_error(self):
        vendor = Vendor.objects.create(name_ar='مورد')
        response = self.client.post(
            reverse('staff_vendor_participation_new', kwargs={'vendor_id': vendor.id}),
            {
                'title_ar': 'مشاركة',
                'starts_at': 'bad-date',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'صيغة وقت البداية غير صحيحة.')
