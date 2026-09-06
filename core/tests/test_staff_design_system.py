from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class StaffDesignSystemContractTests(SimpleTestCase):
    def setUp(self):
        self.root = Path(settings.BASE_DIR)
        self.base = (self.root / 'templates/base.html').read_text(encoding='utf-8')
        self.css = (self.root / 'static/css/staff_foundation.css').read_text(encoding='utf-8')
        self.js = (self.root / 'static/js/staff_responsive_tables.js').read_text(encoding='utf-8')
        self.v2_css = (self.root / 'static/css/staff_ui_v2.css').read_text(encoding='utf-8')
        self.v2_js = (self.root / 'static/js/staff_ui_v2.js').read_text(encoding='utf-8')
        self.pos_css = (self.root / 'static/css/staff_pos_v2.css').read_text(encoding='utf-8')
        self.shell = (self.root / 'templates/includes/staff_shell_nav.html').read_text(encoding='utf-8')
        self.operations = (self.root / 'templates/staff/home.html').read_text(encoding='utf-8')

    def test_staff_foundation_is_loaded_after_existing_styles(self):
        marker = "{% static 'css/staff_foundation.css' %}"
        self.assertIn(marker, self.base)
        self.assertGreater(
            self.base.index(marker),
            self.base.index("{% static 'css/public_menu_flow.css' %}"),
        )

    def test_staff_v2_is_final_staff_stylesheet_and_is_staff_scoped(self):
        foundation = "{% static 'css/staff_foundation.css' %}"
        marker = "{% static 'css/staff_ui_v2.css' %}"
        self.assertIn(marker, self.base)
        self.assertGreater(self.base.index(marker), self.base.index(foundation))
        self.assertIn('.hub-staff-ui{', self.v2_css)
        self.assertIn('--staff-v2-bg:#f7f4ef', self.v2_css)
        self.assertIn('--staff-v2-page:1480px', self.v2_css)

    def test_staff_scope_is_applied_only_to_staff_shell(self):
        self.assertIn('hub-staff-ui', self.base)
        self.assertIn('request.path|slice:":7" == "/staff/"', self.base)
        self.assertIn('.hub-staff-ui .hub-section:not(.hub-card)', self.css)
        self.assertIn('staff-app-header', self.shell)
        self.assertIn('Hub Sweida', self.shell)

    def test_compact_navigation_and_filter_contracts_exist(self):
        self.assertIn('.hub-staff-ui .hub-nav-tabs', self.css)
        self.assertIn('flex-wrap: nowrap', self.css)
        self.assertIn('form[method="get"].hub-form-section', self.css)
        self.assertIn('align-items: end', self.css)
        self.assertIn('.staff-v2-toolbar', self.v2_css)
        self.assertIn('.staff-v2-search', self.v2_css)
        self.assertIn('ops-v2__filter-menu', self.operations)

    def test_pos_workspace_is_not_constrained_to_reading_form_width(self):
        self.assertIn('form.staff-pos__form', self.pos_css)
        self.assertIn('width:100%;max-width:none!important', self.pos_css)
        self.assertIn('grid-template-columns:minmax(0,1fr) 320px', self.pos_css)

    def test_mobile_tables_are_progressively_enhanced(self):
        self.assertIn("{% static 'js/staff_responsive_tables.js' %}", self.base)
        self.assertIn("table.classList.add('hub-responsive-table')", self.js)
        self.assertIn("cell.dataset.label = labels[index] || ''", self.js)
        self.assertIn('.hub-staff-ui .hub-responsive-table td::before', self.css)
        self.assertIn('content: attr(data-label)', self.css)

    def test_notifications_are_compact_on_desktop(self):
        self.assertIn('.hub-staff-ui .staff-notifications', self.v2_css)
        self.assertIn('width:max-content', self.v2_css)
        self.assertIn('.hub-staff-ui .staff-notification-dropdown', self.v2_css)

    def test_operations_is_reference_v2_screen(self):
        for contract in (
            'data-operations-v2',
            'staff-v2-page-head',
            'staff-v2-toolbar',
            'ops-v2__stats',
            'ops-v2__account-grid',
            'data-ops-search',
            'data-ops-filter="unpaid"',
            'data-ops-view="list"',
            'data-ops-tab="orders"',
            'new-account-dialog',
            'staff-context-drawer',
        ):
            self.assertIn(contract, self.operations)

    def test_operations_mobile_contract_uses_bottom_navigation_and_bottom_sheet(self):
        self.assertIn('.staff-mobile-nav{position:fixed;display:grid', self.v2_css)
        self.assertIn('grid-template-columns:repeat(5,minmax(0,1fr))', self.v2_css)
        self.assertIn('.staff-context-drawer{inset:auto 0 0 0', self.v2_css)
        self.assertIn('height:min(82dvh,760px)', self.v2_css)

    def test_v2_interactions_are_progressive_and_loaded_globally_for_staff(self):
        self.assertIn("{% static 'js/staff_ui_v2.js' %}", self.base)
        self.assertIn("document.querySelector('[data-operations-v2]')", self.v2_js)
        self.assertIn("data-dialog-open", self.v2_js)
        self.assertIn("data-ops-filter", self.v2_js)
