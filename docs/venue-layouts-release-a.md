# Hub Sweida Venue Layouts — Release A

Staff-only implementation of the uploaded SVG `hub_seating_designer_v02.html`. This is **draft geometry**, NOT an approved seating capacity or reservation system.

## Where to go after deployment
- Open `/staff/layouts/` with a staff account that has the **Events** capability.
- Five editable starter layouts are installed with the migration: Comedy Hybrid, Theatre/Screening, Cabaret, Workshop and Standing. They are samples, not occupancy-certified maps.
- Click **Open and edit template**, or **Create new template**.
- Both floors use one SVG with real metre units; adjust furniture, 2–4 chairs per 50 cm table, aisle location and width, stage size (anchored to back wall), and rotation.
- Choose an Arabic name and event type, then **Save in Hub**. Reload the editor to verify the saved version. **Save as new template** makes another reusable template.
- Back in the library, select a template and an existing event without a layout, then **Copy layout to event**. The event gets its **own independent JSON snapshot**.
- Open the event's staff detail page to edit its specific layout. Changing the master template later will not modify its snapshot.
- Use the editor's **Visitor preview** only as an illustration. It does **not** hold seats or confirm purchases.
- Choose a historical revision and **Restore as new version**; all earlier drafts remain available. A stale browser version returns HTTP 409; reload before saving rather than overwriting someone else's changes. For offline backups, use the editor's existing SVG/JSON download options.

## Safety and data boundaries
- The baseline shell assumes 7×10 m and a two-level plan with provisional stair/void positions; ground floor is based on the reference drawing's ground staircase, not an engineer-approved survey.
- Every stored JSON document must remain `SCHEMATIC_NOT_APPROVED` and `measured=false`; server validation rejects forged approvals and invalid object schemas, but **does not validate egress, collision-free passage, mezzanine load or legal occupancy**.
- Never use `seats_proposed` to update Event.capacity or to sell tickets.
- There is **no public route or seat hold** in Release A. Public `/events/`, attendance request review, reservations, cashier, refunds and event check-in are unchanged.
- Do not copy restricted customer information into seat-design drafts, screenshots or GitHub.

## Deployment and acceptance
1. After green PostgreSQL Django CI, review and merge the PR.
2. Confirm production DB backup and verified restore record, current git revision, and the existing deployment checks; use the established Hub Suite deployment script.
3. Confirm `events.0006` and `events.0007` migrations are applied (they are additive and create only draft layouts).
4. With synthetic events: save/reload a template, clone into event, edit independently, open on narrow mobile viewport, and test rejected stale versions.
5. Confirm existing public events, staff reservations, ticket payment/check-in and media are unaffected.
6. Do not enable public seat assignment until Release B is implemented, concurrent PostgreSQL tests have passed, and venue geometry/clearances/capacity have been independently verified.

## Release B dependency
[Issue #399](https://github.com/Maximad/hub/issues/399): immutable event/performance inventory, accessible public SVG seat selector, atomic expiring holds, table/group selection, existing booking/payment linkage, staff reassignment and check-in. A published map must be explicitly approved, not inferred from an initial prototype.
