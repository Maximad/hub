# Hub Suite 3.1 — staff contextual workflow milestone (Phase 2)

Status: development branch, **not a production rollout**.

## Scope and navigation

The Operations home page stays the primary workspace, but the same account/order/payment drawer is now mounted by the common Django staff shell when the authenticated user has at least one of `visit_manage`, `order_edit` or `cashier`. This applies to the live Orders, Visits and Cashier lists, which now have links with plain `href` fallbacks plus optional contextual fetch URLs.

The drawer is a responsive side panel on desktop and a bottom sheet on phones. Lists, filters and complete routes still work without JavaScript or if a fragment fetch fails. There are no new URLs, tables, permissions or payment services.

## Normal shift — quick actions

- Select a visit from Operations or Visits: review bill, orders and Internet in one place.
- Add another order to that visit using the existing POS URL with preselected visit.
- Collect full or partial payment with the existing consolidated cashier sheet; manager approval, posting and settlement protections are unchanged.
- Change table or link/unlink member from the visit panel, using `staff_visit_detail`'s audited POST logic and an in-place HTMX fragment.
- Select an order from Operations or Orders: increase/decrease quantity, edit quantity and preparation note in a form, or remove an eligible item. This reuses the canonical `staff_order_edit_update_item` / `staff_order_edit_remove_item` handlers, not a new mutation implementation.
- Empty sessions expose a confirmed delete action; the server refuses deletion if orders, internet or a reservation exist.
- Cancel an order with the existing required reason/confirmation. Posted orders, paid orders, stock, discounts and refunds remain governed by existing status checks.
- If HTMX is available, successful item edits/removals stay in the drawer. The current list refreshes when the modified drawer closes to avoid stale totals. Otherwise the original safe redirects remain.

## Operational scalability

Do **not** remove the existing POS, order edit, cashier, visit detail, finance or preparation full pages. They are fallbacks and specialist workspaces for larger teams.

The next approved milestone adds configurable Direct / Team / Event preparation profiles. This Phase 2 does not change status transitions or imitate intermediate steps. Phase 4 remains the broader correction and financial-reversal workflow.

## Security and integrity

No new authorization surface: existing decorators and `user_has_capability` determine action availability, the existing POST services remain authoritative, CSRF protection is retained and GET panels remain nonmutating. Payments still go through the original financial posting/idempotency services. A drawer fragment must never silently bypass blocked edits.

The customer QR/Internet Phase 1 contract and all MikroTik profiles remain untouched.

## Validation

One GitHub CI checkpoint for the complete batch; add focused regression tests for different staff roles, list-to-drawer links, HTMX and non-HTMX order edits, table change activity logging, and deletion safeguards. After eventual deployment, manually check on phone/desktop: Orders, Visits, Cashier, quick item update, active account edits, a cashier-only role, confirmation/cancel, and failed network fallback.

No production deployment or user-data migrations in this branch.
