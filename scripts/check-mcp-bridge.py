#!/usr/bin/env python3
"""Small import/readiness probe used by deployment and operators."""

import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django

django.setup()

from hub_mcp.server import build_server


server = build_server(enforce_auth=False)
registered = sorted(tool.name for tool in server._tool_manager.list_tools())
expected = [
    'hub_capabilities',
    'hub_get_recipe_lines',
    'hub_search_inventory',
    'hub_search_products',
]
if registered != expected:
    raise SystemExit(f'Unexpected MCP tool set: {registered!r}')
print('MCP_BRIDGE_READY')
