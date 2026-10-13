# Customer fast internet — identified visit and selected bill

## Policy

The table is a physical location, the HubVisit is the bill, and each InternetSession
is linked to the exact visit selected by its initiating browser. Joining a table's
existing visit requires the existing PIN; opening a separate visit creates a
separate bill, even on the same table.

Standalone guests provide a display name and telephone number before starting a
new metered or paid-package connection. The contact is saved on HubVisit and
snapshotted on metered sessions. Authentication of name/phone is not claimed.

## Enforcement

- `fast_internet_identity.py` enforces tableless identity in the core start services;
  UI-only checks are insufficient.
- A POST with only a table QR is not account authorization. The customer must
  choose/join a visit before starting.
- The captive portal offers a table-number route or standalone registration;
  customer visit internet forms also collect missing contact details.
- Opening advanced internet options creates no anonymous tableless account.
- Old active sessions are not forcibly disconnected by these validations.
- Metered internet charge materialization continues to use the selected
  `InternetSession.visit`; package sales already use the visit-linked order.
- Staff can search visits by standalone name or phone.

## Release

- Migration 0051 adds nullable-in-practice (blank) contact fields to HubVisit;
  no existing visit is rewritten.
- Does not update MikroTik profiles, RouterOS configuration, existing grants,
  credentials, or production state.
- Run the full CI suite, then staging validation on actual portal-capable phones
  before any production rollout.
- Customer contact data is personal information: existing staff visit permissions
  apply; do not expose it in provider exports or unauthenticated public tables.
