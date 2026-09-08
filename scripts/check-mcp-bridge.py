#!/usr/bin/env python3
"""Small import/readiness probe used by deployment and operators."""

import os
import sys
from pathlib import Path

# When invoked as ``python scripts/check-mcp-bridge.py``, Python puts
# ``/app/scripts`` (not the repository root) at sys.path[0]. Ensure the project
# root is importable so ``config.settings`` and ``hub_mcp`` resolve the same way
# they do under Uvicorn/Gunicorn.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import anyio
import django

django.setup()

from hub_mcp.server import build_server


EXPECTED = [
    'hub_capabilities',
    'hub_get_recipe_lines',
    'hub_search_inventory',
    'hub_search_products',
]


async def main():
    server = build_server(enforce_auth=False)
    registered = sorted(tool.name for tool in await server.list_tools())
    if registered != EXPECTED:
        raise SystemExit(f'Unexpected MCP tool set: {registered!r}')
    print('MCP_BRIDGE_READY')


anyio.run(main)
