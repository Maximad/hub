import os
from unittest import mock

from django.test import Client, TestCase

from audit.integration_auth import generate_token_value
from audit.models import IntegrationRequestLog, IntegrationToken
from core.models import ActivityLog, Category, InventoryItem, Product, ProductRecipeItem


class ManagementApiTests(TestCase):
    def setUp(self):
        self.env = mock.patch.dict(
            os.environ,
            {
                'HUB_MANAGEMENT_API_ENABLED': 'true',
                'HUB_MANAGEMENT_API_REQUIRE_HTTPS': 'true',
                'HUB_MANAGEMENT_API_CONFIRMATION_TTL_SECONDS': '600',
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

        self.client = Client()
        self.category = Category.objects.create(name_ar='اختبار')
        self.product = Product.objects.create(
            category=self.category,
            name_ar='منتج اختبار',
            price_syp=100,
            product_type=Product.ProductType.FOOD,
            item_type=Product.ItemType.FOOD,
        )
        self.inventory_item = InventoryItem.objects.create(
            code='TEST-ING',
            name_ar='مكوّن اختبار',
            unit=InventoryItem.Unit.KG,
            estimated_unit_cost_syp=50,
        )
        ProductRecipeItem.objects.create(
            product=self.product,
            inventory_item=self.inventory_item,
            quantity_per_unit='0.100',
            unit=InventoryItem.Unit.KG,
        )

    def _token(self, *scopes):
        prefix, digest, complete = generate_token_value()
        record = IntegrationToken.objects.create(
            name='test integration',
            prefix=prefix,
            secret_digest=digest,
            scopes=list(scopes),
        )
        return record, complete

    def _get(self, path, complete_token=None, **extra):
        headers = {'HTTP_AUTHORIZATION': f'Bearer {complete_token}'} if complete_token else {}
        return self.client.get(path, secure=True, **headers, **extra)

    def _post(self, path, data, complete_token=None):
        headers = {'HTTP_AUTHORIZATION': f'Bearer {complete_token}'} if complete_token else {}
        return self.client.post(path, data=data, content_type='application/json', secure=True, **headers)

    def test_disabled_api_fails_closed(self):
        _, token = self._token('schema.read')
        with mock.patch.dict(os.environ, {'HUB_MANAGEMENT_API_ENABLED': 'false'}, clear=False):
            response = self._get('/api/v1/management/schema/', token)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error']['code'], 'integration_disabled')

    def test_missing_bearer_token_is_rejected(self):
        response = self._get('/api/v1/management/catalog/products/')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['error']['code'], 'unauthorized')

    def test_plain_http_is_rejected(self):
        _, token = self._token('catalog.read')
        response = self.client.get(
            '/api/v1/management/catalog/products/',
            HTTP_AUTHORIZATION=f'Bearer {token}',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error']['code'], 'https_required')

    def test_scope_is_enforced(self):
        _, token = self._token('catalog.read')
        response = self._get('/api/v1/management/inventory/items/', token)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error']['code'], 'insufficient_scope')

    def test_read_endpoints_return_operational_master_data(self):
        _, token = self._token('catalog.read', 'inventory.read', 'recipes.read')

        products = self._get('/api/v1/management/catalog/products/', token)
        self.assertEqual(products.status_code, 200)
        self.assertEqual(products.json()['count'], 1)
        self.assertEqual(products.json()['items'][0]['name_ar'], 'منتج اختبار')
        self.assertEqual(products.json()['items'][0]['recipe_line_count'], 1)

        inventory = self._get('/api/v1/management/inventory/items/', token)
        self.assertEqual(inventory.status_code, 200)
        self.assertEqual(inventory.json()['items'][0]['code'], 'TEST-ING')
        self.assertEqual(inventory.json()['items'][0]['recipe_line_count'], 1)

        recipes = self._get('/api/v1/management/recipes/', token)
        self.assertEqual(recipes.status_code, 200)
        self.assertEqual(recipes.json()['items'][0]['product']['name_ar'], 'منتج اختبار')
        self.assertEqual(recipes.json()['items'][0]['inventory_item']['code'], 'TEST-ING')

    def test_catalog_write_requires_preview_then_apply(self):
        token_record, token = self._token('catalog.write')
        preview = self._post(
            '/api/v1/management/catalog/preview/',
            {
                'identifiers': [self.product.pk],
                'action': 'set_exact_price',
                'value': '125',
            },
            token,
        )
        self.assertEqual(preview.status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.price_syp, 100)
        self.assertEqual(preview.json()['preview']['changes'][0]['changes']['price_syp']['after'], 125)

        apply_response = self._post(
            '/api/v1/management/catalog/apply/',
            {'confirmation_token': preview.json()['confirmation_token']},
            token,
        )
        self.assertEqual(apply_response.status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.price_syp, 125)

        activity = ActivityLog.objects.get(pk=apply_response.json()['applied']['activity_log_ids'][0])
        self.assertEqual(activity.details['integration']['token_id'], token_record.pk)
        self.assertEqual(activity.details['integration']['token_prefix'], token_record.prefix)
        self.assertTrue(activity.details['integration']['request_id'])

    def test_confirmation_token_cannot_be_reused_by_another_integration(self):
        _, token_a = self._token('catalog.write')
        _, token_b = self._token('catalog.write')
        preview = self._post(
            '/api/v1/management/catalog/preview/',
            {'identifiers': [self.product.pk], 'action': 'set_exact_price', 'value': '130'},
            token_a,
        )
        response = self._post(
            '/api/v1/management/catalog/apply/',
            {'confirmation_token': preview.json()['confirmation_token']},
            token_b,
        )
        self.assertEqual(response.status_code, 403)
        self.product.refresh_from_db()
        self.assertEqual(self.product.price_syp, 100)

    def test_inactive_token_is_rejected(self):
        record, token = self._token('catalog.read')
        record.is_active = False
        record.save(update_fields=['is_active'])
        response = self._get('/api/v1/management/catalog/products/', token)
        self.assertEqual(response.status_code, 401)

    def test_request_body_and_bearer_secret_are_not_logged(self):
        _, token = self._token('catalog.write')
        response = self._post(
            '/api/v1/management/catalog/preview/',
            {'identifiers': [self.product.pk], 'action': 'set_exact_price', 'value': '777'},
            token,
        )
        self.assertEqual(response.status_code, 200)
        log = IntegrationRequestLog.objects.latest('created_at')
        self.assertEqual(log.path, '/api/v1/management/catalog/preview/')
        self.assertFalse(hasattr(log, 'request_body'))
        self.assertNotIn(token, str(log))
