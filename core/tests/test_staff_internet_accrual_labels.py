"""Active metered Internet estimates must not be presented as settled dues."""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class InternetAccrualPresentationContractTests(SimpleTestCase):
    def test_active_session_detail_separates_estimate_finalization_and_network_wait(self):
        source = Path(settings.BASE_DIR, 'templates', 'staff', 'internet_session.html').read_text(encoding='utf-8')
        self.assertIn("{% if session.status == 'active' %}", source)
        self.assertIn('preview.calculated_total', source)
        self.assertIn('preview.ended_at|date:"Y-m-d H:i"', source)
        self.assertIn('preview.network_waiting', source)
        self.assertIn('غير نهائية', source)
        self.assertIn('لم تُعتمد بعد — راجع التقدير أعلاه', source)
        self.assertIn('لم يُحدد بعد', source)
        self.assertNotIn('<strong>المبلغ المستحق:</strong> {{ session.calculated_total_syp }}', source)
