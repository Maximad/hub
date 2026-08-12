# UX vs. business review

## Purpose and scope

This review compares the experience offered by the current Hub / Masharib product
with the operational and commercial outcomes the system appears designed to
support. It is a code-based heuristic review of the public menu and staff
workflows—not a substitute for interviews, analytics, or observation on site.

The product already covers an unusually broad operating model: customer ordering,
POS, preparation, cashiering, delivery, finance, inventory, members, paid internet,
events, reservations, vendors, and administration. The main product risk is
therefore not missing capability; it is making that capability easy to find and
safe to use during a busy shift.

## Executive assessment

| Perspective | What works | Primary risk |
| --- | --- | --- |
| Customer UX | Arabic-first, table-aware menu and public order tracking provide a coherent self-service foundation. | The business needs conversion and throughput, but the customer journey has no documented funnel or service-level feedback loop. |
| Front-line staff UX | POS, order, kitchen, cashier, and notification surfaces map to real operating roles. | The staff landing page presents operational, analytical, configuration, and occasional tasks at the same visual level, increasing choice and training cost. |
| Management UX | Finance, inventory, reports, close-day, members, and partner workflows give management substantial control. | Breadth can hide exceptions. Managers need a single prioritized view of what requires action, not only destinations to inspect. |
| Business controls | Permission-aware navigation and dedicated posting/reconciliation services show a strong control orientation. | A visible link is not the same as ownership: the interface should state who must resolve an exception and by when. |

**Overall:** the feature set is ahead of the information architecture. The next
investment should organize existing capability around jobs, urgency, and measurable
outcomes before adding more top-level modules.

## Where UX and business goals align

1. **Role-aware access supports both speed and control.** Staff capabilities hide
   many unavailable modules, reducing accidental access while keeping specialized
   workflows in the same application.
2. **The order flow is separated by job.** POS capture, order management, kitchen
   preparation, cashiering, and delivery each have a dedicated surface. This can
   reduce hand-off ambiguity when statuses and responsibilities are consistently
   defined.
3. **Operational records connect to financial controls.** Purchases, expenses,
   cash movements, transfers, daily close, reconciliation, and audit concepts are
   represented. This supports traceability rather than treating reporting as an
   afterthought.
4. **The public and staff experiences share one operating system.** Table QR links,
   the public menu, public order status, and staff fulfillment routes reduce the
   risk of disconnected customer and employee data.
5. **Arabic-first RTL design fits the primary operating context.** This reduces a
   fundamental adoption barrier, while bilingual operational labels preserve
   familiarity with terms such as POS and Food Lab.

## Tensions to resolve

### 1. Complete navigation vs. fast navigation — P0

**UX need:** a staff member should identify the next task in seconds, especially on
a phone or shared terminal.

**Business need:** every module must remain discoverable and authorized staff must
be able to reach exception and configuration workflows.

**Current tension:** the staff home mixes daily work (POS, kitchen, cashier),
periodic controls (close day, reports), setup (users, imports, menu tools, QR), and
business lines (internet, events, Food Lab) in one card grid. Permission filtering
helps, but does not communicate frequency or urgency.

**Recommendation:** replace the flat grid with three explicit groups:

- **الآن / Now:** role-specific primary action, active-order counts, waiting-time
  breaches, unpaid orders, and low-stock blockers;
- **اليوم / Today:** reservations, sessions, delivery, purchases, and close-day;
- **الإدارة / Manage:** reports, menu tools, imports, QR, users, settings, vendors,
  and other low-frequency tasks.

Remember the last-used workspace and make it the first suggested action, without
removing the full directory.

**Success measures:** median time from `/staff/` to first productive action,
mis-navigation/backtracking rate, and staff task-completion time by role.

### 2. Alerts vs. interruption — P0

**UX need:** alerts must be understandable, actionable, and quiet when irrelevant.

**Business need:** new orders and operational exceptions cannot be missed.

**Current tension:** global staff controls offer in-app, sound, and browser
notifications, but the experience should distinguish urgent work from informational
events and explain the effect of enabling each channel.

**Recommendation:** define three severity levels with a single owner and direct
action for each event. Reserve persistent sound for events that block service;
batch informational updates. Show a plain-language permission state for browser
notifications, and measure acknowledgement rather than delivery alone.

**Success measures:** acknowledgement time by severity, missed/unacknowledged P0
events, notification opt-out rate, and alert-to-action conversion.

### 3. Status richness vs. shared understanding — P0

**UX need:** customers and staff need to know what is happening and what they can do
next.

**Business need:** status data must support routing, accountability, timing, and
reporting.

**Current tension:** orders span public tracking, staff orders, kitchen item status,
cashier payment, and delivery. Without one documented status contract, labels can
drift between screens and teams can interpret the same state differently.

**Recommendation:** publish a canonical service blueprint covering customer-visible
order status, internal order status, preparation-item status, payment state, and
delivery state. For each transition define actor, prerequisite, timestamp, reversal
rule, customer message, and escalation threshold. Display “next action” alongside
state wherever possible.

**Success measures:** orders stuck beyond threshold, manual status corrections,
handoff time, cancellation rate by state, and support questions about order status.

### 4. Financial safety vs. recovery from mistakes — P0

**UX need:** staff need prevention before a mistake and a clear recovery path after
one.

**Business need:** posted transactions, closed periods, reversals, exchange rates,
and reconciliation require strong controls and auditability.

**Current tension:** the domain correctly favors explicit posting and reversal, but
staff-facing actions can become intimidating or lead users to work around the system
unless consequences are explained at the decision point.

**Recommendation:** use a consistent confirmation pattern for consequential actions:
what will change, amount/currency, affected account or stock, whether it can be
reversed, and who may reverse it. After completion, show the record identifier and
the permitted recovery action. Never use a generic “Are you sure?” dialog for a
financial transition.

**Success measures:** reversal rate by workflow, reconciliation failures, time to
resolve finance review items, and off-system correction reports.

### 5. Broad business model vs. coherent product language — P1

**UX need:** labels should predict what is behind them.

**Business need:** Hub / Masharib must support multiple revenue lines and partner
programs without constraining growth.

**Current tension:** terms such as partners, vendors, internet partners, and “Food
Lab / partners” may represent distinct business concepts but can appear overlapping
to staff. Mixed Arabic/English is useful only when deliberate and consistent.

**Recommendation:** create a bilingual product glossary. Give each concept one
preferred Arabic label, one English equivalent, a short definition, an owner, and
examples of what is excluded. Test confusing pairs with staff before renaming data
models. Apply the glossary to navigation, forms, reports, exports, and training.

**Success measures:** wrong-module visits, duplicate/misclassified records, training
questions, and glossary comprehension in five-user tests.

### 6. Flexibility vs. safe defaults — P1

**UX need:** common tasks should work without understanding every configuration
option.

**Business need:** menu presentation, availability, options, pricing, media,
stations, vendors, events, and membership rules must remain configurable.

**Current tension:** the flexible catalog can create invalid or commercially poor
combinations that only become visible late in the customer or preparation journey.

**Recommendation:** add a publish-readiness checklist to menu tools: missing price,
missing/invalid image, unavailable preparation station, option-group conflict,
margin warning, and customer-preview mismatch. Separate draft changes from the live
menu and make the scope of bulk actions explicit before apply.

**Success measures:** menu items failing at checkout, unavailable-item order rate,
post-publish corrections, and preparation exceptions caused by catalog setup.

### 7. Data depth vs. decision support — P1

**UX need:** managers need a short explanation of what changed and what to do.

**Business need:** reporting must improve revenue, margin, cash control, stock
availability, and service quality.

**Current tension:** multiple report destinations and CSV exports provide data, but
the landing experience should prioritize decisions and exceptions.

**Recommendation:** make the management home an exception brief, not a report
catalog. Start with five questions: Are sales and margin on plan? Is cash
reconciled? What is at risk of stocking out? Where is service late? What must be
resolved before close? Every KPI should link to the filtered records behind it.

**Success measures:** unresolved exception age, time to daily close, stockout rate,
waste rate, gross margin variance, and report-to-corrective-action conversion.

## Journey scorecard

Scores are heuristic (1 = weak, 5 = strong) and should be replaced with observed
evidence.

| Journey | UX clarity | Business coverage | Main gap | Next validation |
| --- | :---: | :---: | --- | --- |
| Discover and place a table order | 4 | 4 | Funnel and failure reasons are not defined. | Observe 5 first-time customers on their own phones. |
| Capture a counter order | 4 | 5 | Measure speed and correction effort under peak load. | Time 20 representative POS orders. |
| Prepare and hand off | 3 | 5 | Cross-screen status contract and escalation need validation. | Shadow one peak kitchen shift. |
| Take payment | 3 | 5 | Consequences and recovery should be standardized. | Run mistaken-payment and split-payment scenarios. |
| Fulfill delivery | 3 | 4 | Ownership and overdue escalation need explicit rules. | Trace 10 deliveries end to end. |
| Manage stock and purchasing | 3 | 5 | Exceptions should be surfaced before reports are opened. | Test stockout and partial-receipt scenarios. |
| Reconcile and close day | 3 | 5 | Managers need an exception-first close checklist. | Conduct a close with a cash variance and reversal. |
| Manage members/internet | 3 | 5 | Related concepts and cross-system state need clearer language. | Test activation, entitlement, session, and cancellation. |
| Configure the business | 2 | 5 | Setup tasks compete with daily work in navigation. | Card-sort modules with each staff role. |

## Recommended delivery plan

### Phase 1: establish evidence and contracts (1–2 weeks)

1. Agree on business owners and definitions for order throughput, payment
   completion, stockout, margin, daily close, and notification acknowledgement.
2. Instrument the five critical funnels: menu order, POS order, preparation,
   payment, and close day. Record outcome and reason codes—not free-text customer or
   payment data.
3. Document the canonical order/payment/preparation status blueprint.
4. Run role-based card sorting and five usability sessions with actual devices and
   realistic interruptions.

### Phase 2: reduce operational friction (2–4 weeks)

1. Reorganize staff home into Now, Today, and Manage, with a primary action per
   role.
2. Add actionable severity, ownership, deep links, and acknowledgement measurement
   to notifications.
3. Standardize consequential-action confirmations and success/recovery receipts.
4. Create the bilingual glossary and update the highest-traffic surfaces first.

### Phase 3: improve management decisions (3–6 weeks)

1. Introduce an exception-first management brief with drill-through links.
2. Add menu publish readiness and a live-versus-draft preview contract.
3. Set service-level thresholds and route aging exceptions to accountable roles.
4. Review metrics monthly; remove alerts and dashboard elements that do not result
   in action.

## Measurement guardrails

- Segment operational metrics by order channel, service type, shift, and role, but
  avoid employee league tables that reward premature status changes.
- Pair speed with quality: preparation time with remakes/cancellations, close time
  with reconciliation failures, and POS speed with corrections.
- Define event names and state semantics before implementing dashboards.
- Do not collect notification content, customer contact details, or payment data in
  analytics when a record ID and event outcome are sufficient.
- Establish a baseline before changing navigation so improvement can be attributed
  rather than assumed.

## Decisions required from the business

1. Which role owns an order at each handoff, and who owns an overdue order?
2. What are the service-level targets for counter, table, delivery, and internet
   workflows?
3. Which five exceptions must management see before daily close?
4. Which actions require dual control, and which may be reversed by the original
   operator?
5. Are vendors, general partners, internet partners, and Food Lab partners separate
   concepts in staff language, reporting, and accounting?
6. Which modules are daily, weekly, and setup-only for each role?

Answering these questions is the prerequisite for detailed UI changes. It prevents
the interface from encoding assumptions that look clean in a design review but fail
under real operating conditions.
