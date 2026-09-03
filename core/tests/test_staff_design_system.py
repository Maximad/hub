from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class StaffDesignSystemContractTests(SimpleTestCase):
    def setUp(self):
        self.root = Path(settings.BASE_DIR)
        self.base = (self.root / 'templates/base.html').read_text(encoding='utf-8')
        self.css = (self.root / 'static/css/staff_foundation.css').read_text(encoding='utf-8')
        self.js = (self.root / 'static/js/staff_responsive_tables.js').read_text(encoding='utf-8')

    def test_staff_foundation_is_loaded_after_existing_styles(self):
        marker = "{% static 'css/staff_foundation.css' %}"
        self.assertIn(marker, self.base)
        self.assertGreater(
            self.base.index(marker),
            self.base.index("{% static 'css/public_menu_flow.css' %}"),
        )

    def test_staff_scope_is_applied_only_to_staff_shell(self):
        self.assertIn('hub-staff-ui', self.base)
        self.assertIn('request.path|slice:":7" == "/staff/"', self.base)
        self.assertIn('.hub-staff-ui .hub-section:not(.hub-card)', self.css)

    def test_compact_navigation_and_filter_contracts_exist(self):
        self.assertIn('.hub-staff-ui .hub-nav-tabs', self.css)
        self.assertIn('flex-wrap: nowrap', self.css)
        self.assertIn('form[method="get"].hub-form-section', self.css)
        self.assertIn('align-items: end', self.css)

    def test_mobile_tables_are_progressively_enhanced(self):
        self.assertIn("{% static 'js/staff_responsive_tables.js' %}", self.base)
        self.assertIn("table.classList.add('hub-responsive-table')", self.js)
        self.assertIn("cell.dataset.label = labels[index] || ''", self.js)
        self.assertIn('.hub-staff-ui .hub-responsive-table td::before', self.css)
        self.assertIn('content: attr(data-label)', self.css)

    def test_notifications_are_compact_on_desktop(self):
        self.assertIn('.hub-staff-ui .staff-notifications', self.css)
        self.assertIn('width: fit-content', self.css)
        self.assertIn('.hub-staff-ui .staff-notification-dropdown', self.css)
