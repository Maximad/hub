from datetime import timedelta

from django.test import SimpleTestCase
from django.utils import timezone

from core.models import OrderItem
from core.templatetags.prep_timing import prep_timing


class PrepTimingTests(SimpleTestCase):
    def item(self, status, *, created_minutes=0, updated_minutes=0):
        now = timezone.now()
        item = OrderItem(prep_status=status)
        item.created_at = now - timedelta(minutes=created_minutes)
        item.updated_at = now - timedelta(minutes=updated_minutes)
        return item, now

    def test_unacknowledged_item_warns_then_becomes_late(self):
        item, now = self.item(OrderItem.PrepStatus.NEW, created_minutes=3)
        self.assertEqual(prep_timing(item, now)['state'], 'warning')
        item.created_at = now - timedelta(minutes=5)
        result = prep_timing(item, now)
        self.assertEqual(result['state'], 'late')
        self.assertIn('لم يتم الاستلام', result['label'])

    def test_accepted_item_warns_when_prep_has_not_started(self):
        item, now = self.item(OrderItem.PrepStatus.ACCEPTED, updated_minutes=5)
        result = prep_timing(item, now)
        self.assertEqual(result['state'], 'warning')
        self.assertIn('لم يبدأ التحضير', result['label'])

    def test_preparing_item_is_late_after_launch_target(self):
        item, now = self.item(OrderItem.PrepStatus.PREPARING, updated_minutes=12)
        result = prep_timing(item, now)
        self.assertEqual(result['state'], 'late')
        self.assertIn('التحضير متأخر', result['label'])

    def test_ready_item_warns_when_waiting_for_pickup(self):
        item, now = self.item(OrderItem.PrepStatus.READY, updated_minutes=3)
        result = prep_timing(item, now)
        self.assertEqual(result['state'], 'warning')
        self.assertIn('جاهز وينتظر الاستلام', result['label'])
