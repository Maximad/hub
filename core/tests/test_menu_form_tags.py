from django.http import QueryDict
from django.test import SimpleTestCase

from core.templatetags.menu_form import option_selected


class MenuFormTagTests(SimpleTestCase):
    def test_option_selected_supports_plain_mapping_initial_values(self):
        values = {
            'option_12_7': '44',
            'option_12_8[]': ['51', '52'],
        }

        self.assertTrue(option_selected(values, 12, 7, 44))
        self.assertTrue(option_selected(values, 12, 8, 52))
        self.assertFalse(option_selected(values, 12, 8, 53))

    def test_option_selected_still_supports_querydict_post_values(self):
        values = QueryDict('', mutable=True)
        values.setlist('option_12_8[]', ['51', '52'])

        self.assertTrue(option_selected(values, 12, 8, 51))
        self.assertTrue(option_selected(values, 12, 8, 52))
        self.assertFalse(option_selected(values, 12, 8, 53))
