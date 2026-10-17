# Automated USD market-rate pilot (manual approval only)

The **unapproved** rate collection workflow is `.github/workflows/daily-exchange-rates.yml`.
It fetches the public keyless LiraScope `/api/v1/rates/latest?currencies=USD&lang=en`
endpoint. The provider's documentation advertises public access but has changed
limits between published versions; confirm reachability/terms in a real GitHub
Actions run. Never infer that the provider supplies city-specific Damascus prices:
the market feed currently has no verified city field.

## Flow

1. The GitHub workflow (daily around **20:10 Damascus time**) fetches market USD
   buy/sell rates, rejects old-lira-scale numbers, stale timestamps, ambiguous
   denominations, invalid spreads or manual source overrides, then commits
   `rates/latest.json` and `rates/history/YYYY-MM-DD.json` to
   `data/exchange-rates`. It does **not** alter main or the live server.
2. Hub Suite's **read-only HTTPS** importer fetches that public JSON:
   `python manage.py stage_exchange_rate_feed --dry-run`, then
   `python manage.py stage_exchange_rate_feed`. It stages only;
   no transaction, exchange rate or ledger posting is created.
3. The superuser visits **/staff/settings/exchange-rates/**, sets
   the signed **Sweida difference per USD**, and approves/rejects each candidate.
   Approval creates a new immutable `ExchangeRate` with the midpoint of buy and
   sell **plus the local difference**. The existing app's single-reference
   conversion continues to use that rate; directional cash-exchange buy/sell
   is **not** inferred or automated.
4. Approval is explicitly manual; it does not recognize monthly gains/losses,
   recalculate past prices or change open USD liabilities. For accounting,
   consult `docs/finance/currency-safety.md` and obtain a separate approval
   policy before any realized FX postings.

## Opt-in VPS staging schedule (not automatically installed)

Only after the release is merged, tested, deployed and live network access
verified, the operator may install:

```bash
sudo install -m 644 ops/systemd/hub-rate-stage.service /etc/systemd/system/
sudo install -m 644 ops/systemd/hub-rate-stage.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hub-rate-stage.timer
sudo systemctl list-timers hub-rate-stage.timer
```

It schedules **staging** at approximately **21:15 Damascus time** and never
approves or changes the adopted rate. Requires `deploy` access to Docker
and the production compose path; confirm these before enabling.

To test explicitly (from `/opt/hub`):

```bash
docker compose -f docker-compose.prod.yml --env-file .env exec -T web \
  python manage.py stage_exchange_rate_feed --dry-run
docker compose -f docker-compose.prod.yml --env-file .env exec -T web \
  python manage.py stage_exchange_rate_feed
```

An unreachable source, future timestamp, stale rate, invalid JSON, or 100× old-lira
magnitude fails closed. The currently approved rate remains unchanged. If no
candidate exists, diagnose the GitHub Actions collector before enabling the VPS
timer. There is no API key in the workflow. Never move USD amounts to an
unlabeled SYP field or backfill historical financial entries automatically.

**Release acceptance:** workflow executes on GitHub and returns a real JSON
feed (not just documentation), tested approval with a live rate, invalid-rate
injection and retries, audit of the published rate, and a short supervised
cashier test. Neither GitHub data commits nor importing candidates authorizes
automatic accounting or production deployment.
