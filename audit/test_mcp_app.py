from django.test import SimpleTestCase


class HubMCPASGIAppTests(SimpleTestCase):
    def test_asgi_app_imports(self):
        from hub_mcp.app import app

        self.assertTrue(callable(app))
