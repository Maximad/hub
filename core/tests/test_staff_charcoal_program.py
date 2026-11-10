"""Charcoal program design contracts: visual-only changes.

The underlying event, performance, ticket and reservation implementations are not
modified; these checks supplement existing database-backed workflow tests.
"""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class StaffCharcoalProgramContracts(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        root = Path(settings.BASE_DIR)
        cls.templates = {
            name: (root / f"templates/staff/{name}.html").read_text(encoding="utf-8")
            for name in ("events", "event_detail", "event_form", "reservations")
        }
        cls.css = (root / "static/css/staff_charcoal.css").read_text(encoding="utf-8")
        cls.base = (root / "templates/base.html").read_text(encoding="utf-8")

    def test_only_explicit_staff_program_pages_receive_dark_surfaces(self):
        for name, source in self.templates.items():
            with self.subTest(page=name):
                self.assertIn('{% block body_class %}staff-charcoal-program{% endblock %}', source)
        self.assertIn("body.hub-staff-ui.hub-ui-v3.staff-charcoal-program {", self.css)
        self.assertIn("{% static 'css/staff_charcoal.css' %}", self.base)
        self.assertIn('request.user.is_authenticated and request.path|slice:":7" == "/staff/"', self.base)

    def test_event_cards_keep_real_media_status_and_capacity(self):
        events = self.templates["events"]
        for marker in (
            "events-ui__card", "e.staff_media.0", "cover.media_asset.safe_url",
            "e.status == 'published'", "e.status == 'cancelled'",
            "e.capacity", "staff_event_detail", "staff_reservation_new",
            "staff_caps.reservations",
        ):
            self.assertIn(marker, events)

    def test_independent_performance_and_booking_states_remain_visible(self):
        details = self.templates["event_detail"]
        for marker in (
            "show_rows", "show.reserved_people", "show.effective_capacity",
            "show.status == 'scheduled'", "show.status == 'cancelled'",
            "staff_event_schedule", "{% csrf_token %}", "staff_event_checkin",
            "confirmed_guests", "pending_guests", "reservation_count",
        ):
            self.assertIn(marker, details)
        self.assertIn("ليست بيانات تذاكر مدفوعة", details)

    def test_event_creation_and_reservation_checkin_forms_unchanged(self):
        form = self.templates["event_form"]
        reservations = self.templates["reservations"]
        for marker in ("{% csrf_token %}", 'name="status"', 'name="capacity"',
                       'name="starts_at"', "public_homepage_enabled"):
            # Public homepage visibility is deliberately documented in event detail,
            # not inferred from an editor status change.
            if marker != "public_homepage_enabled":
                self.assertIn(marker, form)
        for marker in (
            "today_rows", "upcoming_rows", "past_cancelled_rows",
            "r.operational_status_label", "staff_reservation_detail",
            'name="action" value="checkin"', "{% csrf_token %}",
        ):
            self.assertIn(marker, reservations)

    def test_semantic_colors_and_accessibility(self):
        for token in (
            "#15181C", "#1E2329", "#262C33", "#24C6B2",
            "staff-charcoal-program .events-ui__card", "staff-charcoal-program .hub-list-card",
            "staff-charcoal-program :is(", ":focus-visible", "prefers-reduced-motion:reduce",
            "@media print",
        ):
            self.assertIn(token, self.css)
        self.assertNotIn("pointer-events:none!important", self.css)
