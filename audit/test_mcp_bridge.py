from asgiref.sync import async_to_sync
from django.test import TransactionTestCase
from mcp import Client

from core.models import Category, InventoryItem, Product, ProductRecipeItem
from hub_mcp.server import build_server


class HubMCPBridgeTests(TransactionTestCase):
    """Exercise MCP calls across the SDK worker-thread database connection.

    TransactionTestCase is intentional here: the MCP client may execute sync
    tools in another thread/connection, so setup rows must be committed and
    visible outside the test method's connection.
    """

    def setUp(self):
        self.category = Category.objects.create(name_ar='اختبار MCP')
        self.product = Product.objects.create(
            category=self.category,
            name_ar='منتج MCP',
            price_syp=125,
            product_type=Product.ProductType.FOOD,
            item_type=Product.ItemType.FOOD,
        )
        self.inventory = InventoryItem.objects.create(
            code='MCP-ING',
            name_ar='مكوّن MCP',
            unit=InventoryItem.Unit.KG,
            estimated_unit_cost_syp=50,
        )
        ProductRecipeItem.objects.create(
            product=self.product,
            inventory_item=self.inventory,
            quantity_per_unit='0.100',
            unit=InventoryItem.Unit.KG,
        )

    def test_read_tools_are_protocol_visible_and_query_live_models(self):
        async def scenario():
            server = build_server(enforce_auth=False)
            async with Client(server, raise_exceptions=True) as client:
                listed = await client.list_tools()
                names = {tool.name for tool in listed.tools}
                self.assertEqual(
                    names,
                    {
                        'hub_capabilities',
                        'hub_search_products',
                        'hub_search_inventory',
                        'hub_get_recipe_lines',
                    },
                )

                products = await client.call_tool(
                    'hub_search_products',
                    {'q': 'منتج MCP', 'limit': 10},
                )
                self.assertFalse(products.is_error)
                self.assertEqual(products.structured_content['count'], 1)
                self.assertEqual(products.structured_content['items'][0]['name_ar'], 'منتج MCP')
                self.assertEqual(products.structured_content['items'][0]['recipe_line_count'], 1)

                inventory = await client.call_tool(
                    'hub_search_inventory',
                    {'q': 'MCP-ING', 'used_in_recipes_only': True},
                )
                self.assertFalse(inventory.is_error)
                self.assertEqual(inventory.structured_content['count'], 1)
                self.assertEqual(inventory.structured_content['items'][0]['code'], 'MCP-ING')

                recipe = await client.call_tool(
                    'hub_get_recipe_lines',
                    {'product': 'منتج MCP'},
                )
                self.assertFalse(recipe.is_error)
                self.assertEqual(recipe.structured_content['count'], 1)
                self.assertEqual(
                    recipe.structured_content['items'][0]['inventory_item']['code'],
                    'MCP-ING',
                )

        async_to_sync(scenario)()

    def test_capabilities_advertise_read_only_mcp_boundary(self):
        async def scenario():
            server = build_server(enforce_auth=False)
            async with Client(server, raise_exceptions=True) as client:
                result = await client.call_tool('hub_capabilities', {})
                self.assertFalse(result.is_error)
                self.assertFalse(result.structured_content['mcp_write_tools_enabled'])
                self.assertFalse(result.structured_content['finance_writes'])
                self.assertFalse(result.structured_content['inventory_writes'])
                self.assertFalse(result.structured_content['recipe_writes'])

        async_to_sync(scenario)()
