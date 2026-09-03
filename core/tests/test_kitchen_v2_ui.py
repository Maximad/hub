from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from core.views.kitchen import KANBAN_LANES, STATUS_FILTERS


class KitchenV2UIContractTests(SimpleTestCase):
    def test_status_first_board_contract(self):
        self.assertEqual(
            [(key, label) for key, label, _statuses in KANBAN_LANES],
            [('incoming', 'جديد'), ('working', 'قيد التحضير'), ('ready', 'جاهز')],
        )
        self.assertEqual(STATUS_FILTERS[0], ('all_visible', 'الكل'))

    def test_kitchen_template_uses_v2_shell_and_live_board(self):
        source = (Path(settings.BASE_DIR) / 'templates/staff/kitchen.html').read_text(encoding='utf-8')
        for marker in [
            'kitchen-v2-page',
            'kitchen-v2__status-tabs',
            'kitchen-v2__filters',
            'id="kitchen-board"',
            'hx-trigger="load, every 5s"',
        ]:
            with self.subTest(marker=marker):
                self.assertIn(marker, source)

    def test_partial_renders_three_lane_ticket_grammar(self):
        source = (Path(settings.BASE_DIR) / 'templates/staff/kitchen_partial.html').read_text(encoding='utf-8')
        for marker in [
            'kitchen-v2__kanban',
            'kitchen-v2__kanban--{{ status_filter }}',
            'kitchen-v2__lane--{{ lane.key }}',
            'kitchen-v2__ticket--{{ lane.key }}',
            'kitchen-v2__ticket-actions',
            'row.station_label',
        ]:
            with self.subTest(marker=marker):
                self.assertIn(marker, source)

    def test_mobile_css_stacks_status_lanes_and_focused_filters_hide_other_lanes(self):
        source = (Path(settings.BASE_DIR) / 'static/css/staff_kitchen_v2.css').read_text(encoding='utf-8')
        self.assertIn('@media(max-width:900px)', source)
        self.assertIn('.kitchen-v2__kanban,.kitchen-v2__kanban--active{grid-template-columns:1fr', source)
        self.assertIn('@media(max-width:700px)', source)
        self.assertIn('.kitchen-v2__tickets{grid-template-columns:1fr', source)
        self.assertIn('.kitchen-v2__kanban--working .kitchen-v2__lane:not(.kitchen-v2__lane--working)', source)
        self.assertIn('.kitchen-v2__kanban--ready .kitchen-v2__lane:not(.kitchen-v2__lane--ready)', source)

    def test_staff_primary_actions_have_final_contrast_guard(self):
        base = (Path(settings.BASE_DIR) / 'templates/base.html').read_text(encoding='utf-8')
        source = (Path(settings.BASE_DIR) / 'static/css/staff_ui_v2_contrast_fix.css').read_text(encoding='utf-8')
        self.assertIn("css/staff_ui_v2_contrast_fix.css", base)
        self.assertIn('.hub-staff-ui .staff-v2-button--primary', source)
        self.assertIn('.hub-staff-ui .hub-button-primary', source)
        self.assertIn('color:#fff!important', source)
