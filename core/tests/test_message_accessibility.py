from html.parser import HTMLParser

from django.contrib.messages import constants
from django.contrib.messages.storage.base import Message
from django.template.loader import render_to_string
from django.test import SimpleTestCase, override_settings


class _MessageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.messages = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "div" and "hub-message" in attributes.get("class", "").split():
            self._current = {"role": attributes.get("role"), "text": ""}

    def handle_data(self, data):
        if self._current is not None:
            self._current["text"] += data

    def handle_endtag(self, tag):
        if tag == "div" and self._current is not None:
            self.messages.append(self._current)
            self._current = None


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class MessageAccessibilityTests(SimpleTestCase):
    def test_base_template_assigns_a_live_region_to_each_message_by_level(self):
        messages = [
            Message(constants.SUCCESS, "Saved", extra_tags="custom"),
            Message(constants.INFO, "For your information"),
            Message(constants.WARNING, "Please check this"),
            Message(constants.ERROR, "Could not save", extra_tags="custom"),
        ]

        parser = _MessageParser()
        parser.feed(render_to_string("base.html", {"messages": messages}))

        self.assertEqual(
            parser.messages,
            [
                {"role": "status", "text": "Saved"},
                {"role": "status", "text": "For your information"},
                {"role": "status", "text": "Please check this"},
                {"role": "alert", "text": "Could not save"},
            ],
        )
