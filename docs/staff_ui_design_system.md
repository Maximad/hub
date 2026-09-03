# Hub Staff UI Design System

This document is the presentation contract for all authenticated `/staff/` screens.
It complements `static/css/hub.css`, `static/css/staff_workspace.css`, and the final scoped layer in `static/css/staff_foundation.css`.

## Core principle

Design by function and available space, not by individual device models or page-by-page exceptions.

- Mobile is the baseline: one readable column, touch-safe controls, no squeezed desktop layouts.
- Desktop uses deliberate information density: multi-column grids, compact utility bars, readable form widths, and proper data tables.
- A semantic section is layout, not automatically a bordered full-width card.
- Small statuses, counts, alerts and categories should use chips/badges near the thing they describe, not occupy large panels.
- Search and filter controls are utilities; they should consume as little vertical space as practical.
- Business logic must never depend on presentation classes.

## Shell

Every authenticated staff page receives the `hub-staff-ui` body class from `templates/base.html`.
All global staff-specific rules must be scoped under that class.

The staff shell consists of:

1. compact brand header;
2. primary operational navigation;
3. compact notification utility;
4. centered responsive content container;
5. mobile bottom navigation below the desktop breakpoint.

Do not create additional page-specific top navigation bars unless the screen represents a distinct specialist workspace.

## Page hierarchy

Preferred structure:

```html
<header class="hub-page-header">
  <div class="hub-page-header__main">
    <h2 class="hub-page-header__title">عنوان الصفحة</h2>
    <p class="hub-page-header__meta">سياق مختصر فقط</p>
  </div>
  <div class="hub-toolbar">...</div>
</header>
```

Use `hub-section` for spacing/grouping. Do not add `hub-card` merely to put a border around a whole page section.

Use `hub-card` / `hub-panel` only when content is genuinely one bounded object or task:

- one account;
- one order;
- one form;
- one summary panel;
- one operational card.

## Cards and metrics

Use:

- `hub-grid hub-grid-2` for two peer panels;
- `hub-grid hub-grid-3` for three peer panels;
- `hub-summary-cards` + `hub-summary-card` for short metrics;
- `hub-badge` / `hub-status-chip` for small state information.

A badge is not a card. Avoid wrapping one count/status in a large full-width container.

## Navigation and categories

Use `hub-nav-tabs` + `hub-nav-chip` for local sections/categories.

Rules:

- single horizontal row where possible;
- horizontal scrolling when the number of chips exceeds available width;
- never allow a local category list to become a tall multi-row navigation block;
- add `aria-current="page"` when the current route is known.

## Filters and search

Use GET forms for filters/search:

```html
<form class="hub-form-section" method="get">
  ...
</form>
```

The global design system automatically treats GET forms as compact responsive filter bars on desktop.

Rules:

- prefer 1–4 controls visible at once;
- avoid large blank regions around filters;
- submit/reset actions remain compact;
- mobile stacks controls naturally;
- advanced/rare filters should use `<details>` rather than permanently occupying space.

Do not use inline `display`, `width`, `gap` or margin styles for new filter forms.

## Create/edit forms

Use `hub-form` / `hub-form-section`.

Create/edit forms intentionally have a readable maximum width on desktop. A text input should not stretch across a 1400px monitor simply because space exists.

Use `hub-form-grid` or `hub-grid hub-grid-2` for logically paired fields. The global mobile contract collapses these to one column.

## Data tables

Use:

```html
<div class="hub-table-wrap">
  <table class="hub-table">...</table>
</div>
```

Desktop:

- real table layout;
- compact row density;
- clear header surface;
- horizontal overflow only when necessary.

Mobile:

`staff_responsive_tables.js` automatically maps table headers to `data-label` attributes and the shared CSS renders each row as a readable card.

Do not build a separate mobile copy of the same table unless the information architecture itself needs to be different.

For extremely specialized matrices where row-card conversion is inappropriate, add a dedicated opt-out class and document the reason.

## Buttons and actions

Use semantic Hub button classes rather than custom inline CSS.

- primary: main action for the current task;
- secondary/default: normal navigation or secondary action;
- danger: destructive action only;
- keep buttons close to the object they affect;
- avoid multiple full-width buttons on desktop;
- mobile may stretch important actions where that improves touch use.

Routine staff workflows should not require navigation to separate modules if the action belongs to the current account/context drawer.

## Responsive breakpoints

The system uses content-driven breakpoints, with these main layout thresholds:

- `< 760px`: phone/narrow layout;
- `< 900px`: mobile navigation / single-column operational workspace;
- `>= 760px`: compact desktop/tablet forms and filter bars;
- larger grids use available space rather than targeting specific device brands.

Breakpoints should be adjusted when content stops fitting well, not to match a named phone or laptop model.

## RTL and numbers

- Arabic interface remains RTL.
- Monetary amounts, IDs, durations and other numeric runs use the existing Latin-safe number classes and tabular numerals.
- Never force an entire Arabic table or card to LTR just to fix number rendering.

## Accessibility

- preserve visible keyboard focus;
- minimum practical touch target remains around 40–44px for routine controls;
- do not communicate status using color alone;
- keep semantic table markup even though mobile presentation changes;
- use labels for form inputs;
- respect `prefers-reduced-motion`.

## Avoid

Do not add new:

- inline layout styles;
- full-page bordered `hub-section` containers;
- filters that occupy an entire desktop row when a compact bar works;
- fixed pixel page widths tied to one device;
- duplicate mobile/desktop business markup;
- isolated one-off color/radius/button systems;
- oversized badges or alert cards for one short status;
- horizontal mobile tables as the default when row-card presentation is viable.

## Review checklist for new staff pages

Before merging a new screen, verify:

- desktop at roughly 1280–1440px;
- narrow laptop/tablet around 800–1000px;
- phone around 360–430px;
- no unexpected horizontal page scrolling;
- filters are compact on desktop;
- forms have readable width;
- table rows are understandable on mobile;
- primary action is visually obvious;
- statuses use compact chips/badges;
- empty states do not become giant boxes;
- all interactions remain usable by keyboard and touch.
