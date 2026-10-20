# Hub handover: unresolved operational review briefing

This is a deliberately small read-only extension to the **existing** Business Day handover workspace. The existing Active Duty service is the authority for transferring responsibility; existing `HandoverNote` records carry free-form issues between days; the single `OperationalReviewCase` model is the canonical source for incomplete materials, recipes, product pricing, purchase receipt review and Internet incidents.

## What staff sees

Within `/staff/operations/business-day/` (existing Business Day workspace), the Handover section lists up to eight currently open, source-linked operational review cases. Each row includes only issue type and safe source label, with a link to the authorized operational tasks screen. A truncation message points users to the full tasks view.

- Authorized manager: sees the open follow-up briefing even if the Operations duty slot is vacant.
- Checked-in, capable holder of **accepted** Operations duty: sees only follow-up cases permitted for the holder.
- Proposed recipient, checked-out employee, or other staff: sees no private follow-up cases before acceptance.
- Existing handover notes and their permissions remain unchanged.

The briefing makes no writes: no duty reassignment, task status update, ledger posting, stock correction, message/push, Internet action or notification acknowledgment. An unresolved case is not closed just because it appeared in the briefing.

## Verified tests

- Manager visibility and no database mutation on GET.
- Before and after an **accepted** handover, the correct operational duty holder sees the case; an unaccepted proposal transfers no access.
- Resolved cases disappear from the live briefing.
- The list is bounded; unauthorized staff sees no case labels.

## Intentionally deferred

No new handover ledger or checklist, automatic review-case synchronization, legacy historical financial inference, Internet router changes, or operator push broadcasts. The brief deliberately reuses the existing source-linked cases and the Active Duty/Business Day flow.

Production rollout is a separate step following full CI, merge, deployment backup verification and staff acceptance. The user requested to continue development without production testing for now.
