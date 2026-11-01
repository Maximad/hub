# Hub Suite — public, staff and Admin acceptance overlay
**2 November 2026 · applies to existing audit register and release process, not a new test system**

## Release states (never collapse)
- **Implemented** = branch changes have focused code/tests; not deployed.
- **Merged** = PR merged following green required CI; production behavior not inferred.
- **Deployed** = revision and feature flags independently confirmed on target environment.
- **Verified** = observed on real target, recorded role, timestamp, environment/revision, expected versus actual outcome and evidence link.
- Failed/partial/unavailable tests remain open, never automatically marked PASS.
- Do not alter historical, financial, synthetic AUDIT-20261102, RouterOS or customer records to make tests pass.

## Cross-platform acceptance checks
| Criterion | Minimal check | Evidence / status |
| --- | --- | --- |
| Navigation | Arabic task labels, breadcrumb or equivalent, return link; no disabled/dead CTA; relevant links between source records | Browser screenshot and link-check, **unverified live** |
| Shared components | RTL, keyboard Tab/Shift+Tab, focus and contrast, buttons, inputs, tables, date (Damascus), currency distinction | 360/390/430, tablet and desktop, **unverified live** |
| Error/recovery | Invalid forms keep inputs and give actionable errors; repeated consequential POST is idempotent and produces one audited effect | Existing finance/ordering/internet regression contracts + supervised staging, **not certified** |
| Data readiness | Synthetic/historical/current indicators; cost/count evidence, verified versus unknown; latest versus stale rates | Compare source records, never auto-normalize; **unverified live** |
| Equipment | Thermal and regular receipt printing, QR reading, kitchen/bar screens, phone keyboard/touch, weak/offline connectivity | Actual connected equipment required, **not performed** |
| Full shift | Opening → POS → prep → cashier → purchase/stock → internet → reconciliation → close → handover, plus exceptions | Existing training guide, role-signed staging checklist, **not performed** |
| Adoption | A staff member locates a routine task and finishes it without founder instructions | Observe staff role, time to task, blockers, **not performed** |

## Public homepage release gate (UX-44)
1. Check source publication states and editorial permissions; no internal-only programs, pop-ups or misleading memberships.
2. Review anonymous root, event/detail, menu and member account targets with release flag ON in **staging**; unpublished/private media never appear.
3. Validate Arabic-first mobile widths 320/360/390/430, tablet, desktop, keyboard, images/fallback, empty pages, HTTP 404/403/no-store.
4. Confirm the real venue address, hours and official public contact source before adding them; do not invent a map link.
5. Separate signed editorial approval, deploy evidence, feature flag enablement and live anonymous acceptance. **UX-44 OPEN until published homepage is verified.**

## Django Admin acceptance (UX-42/43)
1. Superuser: locate order, purchase, member, image, event and settings using Arabic/English synonyms with keyboard only.
2. Check seven groups, accurate related-record links, natural back route and visible location; compare list search/filter/row links.
3. Validate long results at desktop and 360/390px without whole-page horizontal overflow; scroll only within the data region.
4. Non-superuser with Django model perms must still be denied direct Admin URLs; staff routes use their existing capability checks.
5. Confirm no financial or inventory write occurred from mere navigation.

## Staff adoption note
**What changed:** Homepage navigation hides public discovery categories without published eligible content. Private previews identify unavailable destinations. Admin opens with task groups and bilingual/synonym search, with contextual order/purchase links and compact common lists.

**How to use:** In Admin, search for a task such as «فاتورة» or `stock`; choose the matching operational screen for work or the Admin record for inspection. Use «دليل الإدارة» to return. Do not enter/modify financial records just to test navigation.

## Evidence to collect
Record: audit ID, operation, environment and deployed SHA, release flags, role, device/browser, synthetic/real fixture, tester, date/time, expected/actual result, screenshot/log reference, verdict PASS/FAIL/PARTIAL/UNVERIFIED and blocker/owner. Do not place identifiable customer data in broad-access Notion pages.
