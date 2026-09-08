# Hub MCP

`hub_mcp` is the remote Model Context Protocol bridge for Hub Suite management data.

The first release is deliberately read-only. It exposes product, inventory and recipe lookups using the same scoped integration credentials as `/api/v1/management/` and runs in its own ASGI sidecar so the existing Django/Gunicorn process is not changed.

See `docs/management-integration/mcp-bridge.md` for rollout and proxy instructions.
