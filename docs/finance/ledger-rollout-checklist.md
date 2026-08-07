# Additive ledger rollout checklist

Old finance columns and compatibility projections remain in place throughout this
initial rollout. The posting service is the sole owner of the new record and its
legacy projection, and must create both in the same database transaction.

## Phase 1 — controlled ledger writes

**Acceptance criteria (automated in `test_posting_rollout.py`):** disabling
`posting_ledger_writes_enabled` (or deployment setting
`POSTING_LEDGER_WRITES_ENABLED=False`) stops the rollout and forces legacy reads;
existing ledger rows are retained.

- [ ] Apply additive migrations; do not remove legacy columns.
- [ ] Run the posting-service and transaction tests.
- [ ] Run `python manage.py reconcile_postings`; classify and resolve every critical discrepancy.
- [ ] Enable writes only after the baseline reconciliation is clean.
- [ ] Roll back by disabling the write flag; never delete new rows.

## Phase 2 — dual-read comparison

**Acceptance criteria:** dual reads cannot be enabled unless Phase 1 writes are on
and no unresolved reconciliation failures exist.

- [ ] Confirm Phase 1 has no unexplained critical discrepancies.
- [ ] Enable `posting_dual_read_enabled` / `POSTING_DUAL_READ_ENABLED`.
- [ ] Compare legacy projections to ledger results; do not use ledger values in reports yet.
- [ ] Investigate every difference and rerun reconciliation.
- [ ] Roll back by disabling either the dual-read or write flag.

## Phase 3 — report cutover

**Acceptance criteria:** report reads cannot be enabled until dual reads are active
and reconciliation remains clean.

- [ ] Attach a clean Phase 2 reconciliation result to the deployment record.
- [ ] Enable `posting_reports_enabled` / `POSTING_REPORT_READS_ENABLED` gradually.
- [ ] Monitor report totals and legacy-versus-ledger comparison metrics.
- [ ] Roll back immediately to legacy reports by disabling report, dual-read, or write flags.
- [ ] Retain all old columns and all new ledger data during the initial rollout.
