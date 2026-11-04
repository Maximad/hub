# Hub Suite domain cutover: hub.nass.life

Status: preparation only. This runbook does not authorize an automatic production cutover.

## Architecture
The existing Ubuntu VPS runs Docker Compose + Traefik + Django + PostgreSQL. This is **not** a Hostinger shared-website migration. Keep the existing VPS, database, proxy, and Docker volumes. Do not add an Nginx instance, recreate the website, or switch the domain's nameservers.

## Preconditions
1. Confirm that the owner controls `nass.life` and identify the **authoritative DNS provider**. Hostinger registration does not imply Hostinger DNS.
2. Verify the current public IPv4 of the **existing** Hub VPS through hPanel/VPS settings or the server. Do not infer it from old documentation.
3. Export or capture the existing `nass.life` DNS zone, preserving every MX, SPF, DKIM, DMARC, TXT, CAA and unrelated subdomain record. No DNS reset.
4. Verify a recent successful production backup and restore evidence and a clean main branch before cutover.
5. Check public URLs and existing MikroTik captive-portal allowlisting for the new hostname. The router is an external dependency: do not rewrite RouterOS profiles, rates, network topology, or its other settings.

## DNS (at the authoritative DNS provider)
Add or update **only** the subdomain record (not the apex):
- Type: `A`
- Host/name: `hub`
- Value: the verified current Hub VPS public IPv4
- TTL: 300 seconds if offered (otherwise default)

If a conflicting `hub` CNAME, A, or AAAA already exists, inspect it before changing it. Do not alter nameservers or email records. Wait until independent DNS checks resolve `hub.nass.life` to the expected server address. There is no need to create a Hostinger shared-hosting website.

## Server configuration
The production `/opt/hub/.env` is not committed. Edit it on the VPS through your normal secure process, preserving all other existing values and secrets.

```dotenv
HUB_PUBLIC_HOST=hub.nass.life
PUBLIC_BASE_URL=https://hub.nass.life
DJANGO_CSRF_TRUSTED_ORIGINS=https://hub.nass.life
MIKROTIK_PORTAL_HOST=hub.nass.life
HUB_MCP_PUBLIC_HOST=hub.nass.life
```

Update `DJANGO_ALLOWED_HOSTS` to include `hub.nass.life` **without removing any required internal/VPN hosts**. If other CSRF origins are genuinely used by staff/admin browsers, preserve those explicitly. Keep `PUBLIC_BASE_URL` and `HUB_PUBLIC_HOST` consistent.

`docker-compose.prod.yml` reads `HUB_PUBLIC_HOST` for the two Traefik host rules, and the deployment script reads the same entry from `.env` for HTTPS smoke checks. When the variable is absent, both retain the old hostname for backward compatibility before cutover.

## Deploy
After DNS is confirmed:
1. Review and merge the domain-preparation PR following the normal staging/CI checks.
2. Validate `docker compose -f docker-compose.prod.yml --env-file .env config --quiet`. Confirm the resolved routing label uses `hub.nass.life` without printing secrets.
3. Run the repository's normal protected production deployment script as the `deploy` user. It backs up and verifies restore evidence, checks Django, applies migrations as needed, recreates the web services, collects static assets, and checks routes.
4. Allow Traefik's existing `letsencrypt` resolver to provision a certificate for the new name. Do not install a separate Nginx or a Hostinger shared-hosting SSL certificate.
5. If the certificate/route fails, inspect Traefik logs and DNS, rather than retrying blind or disabling HTTPS validation.

## Acceptance checks
- `https://hub.nass.life/menu/`: 200, correct menu.
- `https://hub.nass.life/admin/login/`: 200; login succeeds; no CSRF error.
- `https://hub.nass.life/staff/`: unauthenticated redirect, then staff login works.
- Orders, POS, cashier, receipts, kitchen board, and static/media assets work.
- Generated table QR codes, member activation/public URLs and order status links use the new host.
- Fast/basic Internet access and captive-portal redirects work on an **unauthenticated** device. If portal DNS allowlisting must change, make a narrowly scoped, reviewed router-side change outside Django; do not touch speed profiles, rates or topology.
- Optional MCP integration is tested if it is enabled.
- Logs, `smoke_check`, and `internet_readiness` pass.
- New QR labels are printed to replace any labels containing the expired old hostname.

## Recovery
Before cutover preserve the previous nonsecret hostname settings and a verified database backup. Revert the Traefik/Django public-host settings using the standard deployment process if the new hostname is not ready; **note:** an expired `jwtalenthouse.com` cannot serve as a reliable public rollback address. Prioritize correcting DNS/SSL on `hub.nass.life`. Never restore production data solely to fix a hostname.
