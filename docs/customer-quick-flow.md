# Customer quick-flow contract

Hub's customer UX should hide system complexity.  The backend may have several
Internet, identity, billing and visit states, but each screen should expose the
next useful action rather than the whole decision tree.

## Captive landing

The first screen keeps two primary choices:

- **الاتصال بالإنترنت**
- **افتح المنيو**

Opening either choice is read-only until the customer explicitly starts Internet,
joins/creates a visit, or submits an order.

## Internet sheet priority

When the Internet sheet opens, Hub resolves the primary action in this order:

1. an already active browser-bound Internet session;
2. an authenticated staff user's active internal grant;
3. available basic/complimentary Internet;
4. direct metered fast Internet;
5. other paid packages;
6. unavailable state.

Only one primary connection action should dominate the sheet.

Identity recovery and uncommon alternatives belong under the collapsed
**لديك حساب أو تعمل في هَبّ؟** disclosure.

Table number entry is not part of the Internet sheet. Table/bill selection belongs
to the menu/order journey.

## Completion

A successful one-tap Internet start relays the current browser through the existing
HotSpot identity and returns:

- customers to the menu;
- authenticated staff using an internal grant to the staff workspace.

The session screen is for inspection and management, not a mandatory post-connect
step.

## Staff

A staff login started from the captive portal continues directly into the staff
Internet grant when an active grant exists. It must not require Django admin access,
create a commercial sale, or expose another staff user's grant.

## Session screen

Normal **جلستي** keeps only a compact bill summary, quick service actions,
a compact Internet status, and orders.

Detailed package/Internet choices are shown only when the customer explicitly
opens the Internet-options mode.

For metered Internet, the active state states clearly that cost continues until the
connection is stopped. A zero or unfinished running bill must not imply that the
active metered Internet is free.

## Non-negotiable boundaries

The UX simplification must not:

- change RouterOS configuration;
- bypass entitlement/session/device limits;
- create fake payments or orders for internal access;
- merge browser-specific Internet sessions;
- make opening the portal or menu start/bill Internet automatically.
