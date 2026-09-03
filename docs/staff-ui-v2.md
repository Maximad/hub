# Hub Staff UI v2

This is the visual and interaction contract for the Hub staff application.

## Product principles

- Arabic RTL first.
- Warm off-white application background, white/cream working surfaces, charcoal text, restrained Hub green and sand accents.
- Use whitespace to separate concepts, not to inflate controls.
- Prefer thin borders and subtle elevation over large bordered containers.
- A section is not automatically a card.
- Keep the primary navigation small: Operations, Preparation, Inventory, Finance, More.
- Keep filters compact. Desktop filters belong in one toolbar; mobile filters belong behind one action/sheet.
- Desktop may use dense grids and tables. Mobile uses stacked entities and touch-first actions.
- Contextual work opens in a right drawer on desktop and a bottom sheet on mobile.
- Primary touch targets should remain around 44px or larger.

## Layout modes

- Mobile: below 700px — one column, persistent bottom navigation, bottom-sheet context.
- Tablet/small desktop: 700–1100px — reduced grids and simplified shell.
- Desktop: above 1100px — multi-column workspaces, contextual side panels/drawers.

## Component vocabulary

Use the `staff-v2-*` primitives for migrated pages:

- `staff-v2-page`, `staff-v2-page-head`
- `staff-v2-toolbar`, `staff-v2-search`, `staff-v2-segmented`
- `staff-v2-button`, `staff-v2-icon-button`
- `staff-v2-panel`, `staff-v2-panel-head`
- `staff-v2-badge`
- `staff-v2-dialog`

Screen-specific classes may compose these primitives, e.g. `ops-v2-*`.

## Operations reference

Operations is the reference implementation for entity/card-heavy staff workflows:

1. Page heading and only the most important actions.
2. One compact search/filter/view toolbar.
3. Four small KPI cards.
4. Active account cards as the primary workspace.
5. A compact active-orders attention panel.
6. Account/order/payment/Internet work inside the existing contextual drawer.
7. On mobile, the same drawer becomes a bottom sheet and the account grid becomes one column.

## Migration rule

Do not restyle future staff screens by adding page-specific fixes to `hub.css`. Migrate them onto the v2 primitives. Legacy CSS remains temporarily for unmigrated screens and is retired as each screen family moves to v2.
