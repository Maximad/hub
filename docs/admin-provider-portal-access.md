# Admin access to the Internet provider portal

Administrators assigned the existing \`provider_dashboard\` capability can open
\`/provider/\` using their normal Hub login. No external provider role, duplicate
user account or \`InternetPartnerUser\` membership is required.

- The admin lands on the active default partner, or the first active partner
  when no default exists. The partner's name is clearly displayed.
- Admins can switch between **active** partners with the selector at the top
  of provider pages. Switching is an authenticated CSRF-protected POST; the
  selected partner ID is kept only in the admin's own browser session.
- The existing provider dashboard, subscriptions, packages, customers,
  sessions and reports remain scoped to the **selected partner**. Cross-partner
  object links continue to return 404. No global/provider-mixed aggregate
  shortcut is introduced.
- Other provider actions continue to check their own granular capabilities.
  Per-user explicit deny overrides remain effective. Admins with a restricted
  profile that lacks \`provider_dashboard\` remain blocked.
- Provider accounts retain their mandatory active partner association and do
  not get access to the admin selector or staff pages.
- If there is no active provider, access fails closed instead of fabricating a
  partner or exposing unscoped records.
- Admins see customer phone numbers inside the existing portal, consistently
  with their local administrative access. External provider phone visibility
  continues to depend on \`InternetPartnerUser.can_view_customer_phone\`.
- Returning to Hub from the provider portal does **not** log out the
  administrator. The provider login/logout flow for external providers is
  unchanged.

No data migration, router action, network configuration change, new financial
records or staff role reassignment is required. The change becomes live only
after the **Django application** is deployed. Keep the Mikrotik Compose CA
overlay and TLS verification as configured in the previous release.
