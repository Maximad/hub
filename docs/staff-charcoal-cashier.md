# Charcoal cashier directory and consolidated account — Phase 4

**Scope:** `/staff/cashier/` directory and consolidated visit summary only.  
**Base:** `main` after Phase 3 PR #405 merged.  
**Status:** presentation-only draft, not merged/deployed.

## What changes

Two existing staff templates opt into `staff-charcoal-cashier`. The single shared charcoal CSS layer supplies token mapping and scoped card/form/status styles.

- Outstanding and paid balances remain visible with existing labels.
- Account / visit identifiers, search, filters and direct cashier links are not modified.
- Existing partial-payment and settle-and-close sections retain their permissions, original markup, hidden idempotency keys, CSRF, currencies, server-side validation and manager approvals.
- Active metered internet warnings remain visible and are never presented as final settled amounts.
- No financial records, business-day policy or payment flow is altered.
- Standalone payment details (`cashier_order.html`) deliberately remain outside this CSS opt-in until live browser QA.

## Review gate

- Full CI after parent Phase 3 merges; rebase before merge.
- Browser tests with representative unpaid, partially paid, settled and active-metered sessions; compare monetary numbers and action labels.
- Check 360, 390, 430, 800 and desktop widths; Arabic RTL, keyboard and high contrast.
- Execute safe authorized staff test transactions in a test DB only; verify no changed payment allocations or settlement behavior.
- Check printer/thermal receipt routes remain unchanged.
- Do not deploy based only on a GitHub merge.

## Afterward

Payment-detail visual QA, inventory/finance/admin information architecture and public-facing experience require their own scoped work.
