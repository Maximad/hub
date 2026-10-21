# Stock quantity context after a physical count

Base: main `d4dcd5e8b429472fcb7c868cee151e0a32801cf2`. Independent of open PR #333 (individual operational review confirmation) and draft PR #332 (handover briefing).

## Staff outcome

The existing stock-count screen and material list now show the last recorded physical count's timestamp, quantity and original unit alongside the current stored quantity. They distinguish a matching quantity, later movement records, a changed unit, a different unexplained balance and a missing/incomplete count snapshot.

The history link opens the existing movements page filtered to the selected item and records created since that count. An additional link restores all movement history for that item. Old business dates do not hide subsequently entered receipts. The table separates creation time from business date, labels cancelled records, and links a purchase movement to existing inventory purchase detail. CSV remains an explicitly labelled export of all movements.

Count-only users see quantity context and their existing count action. General inventory navigation and movement-detail links require inventory capability. The context does not expose movement notes, actor identities or costs to count-only users. All destinations retain their permission checks.

## Evidence and limits

- Physical quantity snapshots come from existing `AuditEvent` count actions in the `staff-stock-count` channel. Estimated costs, legacy zeros, generic activity entries and unrelated audit actions do not supply a quantity snapshot.
- Use the latest audit's original unit and quantity. Incomplete latest evidence stays incomplete rather than falling back to an older convenient snapshot. The existing `counted_inventory_ids` readiness signal is unchanged and remains separate from this quantity view.
- Movement **recording** time determines whether a record was entered after a count. An equal timestamp is conservatively included. The count's own correction normally precedes its audit and is not counted as a subsequent movement.
- `StockMovement` has no general applied-at marker. A record, including a cancelled one, is not proof it changed the balance or represents current consumption. The UI therefore does not sum recorded rows into an expected balance, infer stock losses, create forecasts, or call a subsequent balance physically counted.
- Two batch queries retrieve at most one latest audit and one latest movement per item. No per-material query loop or full movement-history scan in Python.
- The existing counted-material low-stock gate, operational review cases and daily task generation are unchanged.

## Validation and rollback

Automated coverage uses real signed count confirmation, including repeated unchanged zero counts, to verify snapshot selection. It also covers the count correction boundary, a backdated linked receipt entered later, cancelled/unapplied records, changed units, unexplained balances, a later fresh count, malformed latest evidence, tied timestamps, batch query bounds, cross-item/invalid filter rejection, permission-scoped navigation and repeated reads without stock, audit, case, task or money writes.

Full Django/PostgreSQL CI and mobile-render results are recorded in the PR. This workspace uses CI for database-dependent checks because it cannot start PostgreSQL as a non-root user; it does not use a SQLite substitute.

This package makes read-only UI and query changes. No migrations or data repair, new task models, stock deductions, purchasing, network operations or RouterOS changes. Revert the PR to remove the view; existing source records need no rollback. Production SHA/parity remain unverified, and no production deployment or manual operational testing is part of this package.

Remaining work: document reliable applied/reversed movement evidence through existing posting lifecycles before considering any stronger balance reconciliation; improve bounded purchase/Internet error recovery separately. Historical accounting audit remains later work.
