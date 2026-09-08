# Hub Management MCP Bridge

The MCP bridge exposes the Hub management integration as Model Context Protocol tools while keeping the existing Django web process unchanged.

## Security boundary

The bridge is fail-closed unless both of these are true:

```dotenv
HUB_MANAGEMENT_API_ENABLED=true
HUB_MCP_ENABLED=true
```

It reuses the `IntegrationToken` credential system from the REST management API, so revocation, expiry and SHA-256 secret verification have one source of truth. However, MCP access is **not inherited by ordinary REST credentials**: a token must explicitly carry the `mcp.connect` scope before it can discover the MCP tool surface.

Use a dedicated read-only MCP token rather than reusing a credential that has `catalog.write`.

Transport access requires:

- `mcp.connect`

Each MCP tool then checks its own data scope again:

- `hub_capabilities` -> `schema.read`
- `hub_search_products` -> `catalog.read`
- `hub_search_inventory` -> `inventory.read`
- `hub_get_recipe_lines` -> `recipes.read`

MCP v1 is intentionally read-only. It has no finance writes, inventory writes, recipe writes, destructive operations or arbitrary model access. Catalog writes remain behind the REST `preview -> single-use confirmation -> apply` contract until MCP write tools are separately reviewed.

Authenticated MCP HTTP requests write metadata-only `IntegrationRequestLog` rows. Request bodies and bearer secrets are not stored.

## Runtime

`docker-compose.prod.yml` runs MCP in an isolated ASGI sidecar:

- container: `hub-management-mcp`
- internal port: `8001`
- host loopback: `127.0.0.1:8900`
- ASGI app: `hub_mcp.app:app`

The existing Django/Gunicorn application remains on `127.0.0.1:8899`.

The production deployment script builds, recreates and checks the MCP sidecar together with the application services. `scripts/check-mcp-bridge.py` verifies that the expected MCP tool set can be registered before the deployment is marked successful.

## Environment

Optional settings:

```dotenv
HUB_MCP_ENABLED=false
HUB_MCP_PUBLIC_HOST=hubsweida.jwtalenthouse.com
HUB_MCP_ALLOWED_ORIGINS=https://chatgpt.com,https://chat.openai.com
```

`HUB_MCP_ENABLED` should remain false through the first deployment. Enable it only after the sidecar is healthy and the reverse-proxy route is configured.

## Dedicated MCP credential

Create a separate read-only credential when the bridge is activated:

```bash
python manage.py create_integration_token \
  --name "Hub MCP Read" \
  --scope mcp.connect \
  --scope schema.read \
  --scope catalog.read \
  --scope inventory.read \
  --scope recipes.read \
  --expires-in-days 30 \
  --notes "Read-only MCP integration; no catalog.write or finance/inventory/recipe writes."
```

Do not add `catalog.write` to this credential.

## Reverse proxy

The public MCP URL is intended to be:

```text
https://hubsweida.jwtalenthouse.com/mcp
```

The host proxy must route `/mcp` to `127.0.0.1:8900` before the generic Hub route to `127.0.0.1:8899`.

Example Caddy shape (merge with the existing site block; do not replace unrelated routes):

```caddyfile
hubsweida.jwtalenthouse.com {
    handle /mcp* {
        reverse_proxy 127.0.0.1:8900
    }

    handle {
        reverse_proxy 127.0.0.1:8899
    }
}
```

Preserve the existing production proxy configuration and only add the path-specific MCP route.

## Production activation order

1. Deploy the code with `HUB_MCP_ENABLED=false`.
2. Confirm the `management-mcp` service is healthy and the deployment readiness probe prints `MCP_BRIDGE_READY`.
3. Add the `/mcp` reverse-proxy route.
4. Confirm the public route remains disabled while `HUB_MCP_ENABLED=false`.
5. Create a dedicated token containing `mcp.connect` plus only the required read scopes.
6. Set `HUB_MCP_ENABLED=true` and recreate `management-mcp`.
7. Confirm a credential without `mcp.connect` is rejected.
8. Test MCP discovery and read tools with the dedicated credential.
9. Keep the existing REST management API credential separate for catalog preview/apply writes.

## Development tests

The Django test suite includes an in-memory MCP client test. It exercises real MCP discovery/tool-call behavior against test database models without HTTP auth, while separate tests cover the shared bearer verifier used in production.

Run:

```bash
python manage.py test audit.test_mcp_bridge audit.test_mcp_app audit.test_integration_auth_shared
```

Then run the full PostgreSQL CI suite before production rollout.
