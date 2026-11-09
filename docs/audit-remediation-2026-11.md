# November 2026 audit remediation

Manager selection on transfers and supplier payments now requires the selected
manager's password. The manager must differ from the submitting user. Passwords
are excluded from request-key generation and are not stored in posting metadata.

Table PIN failures are recorded per table in PostgreSQL, so fresh browser sessions
cannot reset the limit. Apply the new `locations.0002_tablepinthrottle` migration.
Five failures lock joining for five minutes; successful joins do not erase failures.

Receipt media and generated card thumbnails require finance capability and return
private/no-store responses. Production must continue routing `/media/` through
Django; serving this directory directly from a proxy bypasses these checks.
Previously cached public receipts may require cache invalidation.

Order collection locks its selected accounts and rechecks the accounting period
before creating a payment. Accounts must be active SYP accounts in
`COLLECTION_BUSINESS_UNIT`. Configure `COLLECTION_CASH_ACCOUNT`,
`COLLECTION_TRANSFER_ACCOUNT`, and `COLLECTION_REVENUE_ACCOUNT`.
Without explicit codes, valid canonical accounts are preferred; otherwise exactly
one eligible account must exist. Ambiguous or invalid configuration fails closed.
Payment entry points use the configured business-day cutoff, including overnight.

Posting retries validate command, source and actor. Order collections, transfer
posting and supplier payments additionally compare their financial intent.
Old receipts without intent metadata reject these retries for manual review;
do not issue a fresh key until the original transaction has been reconciled.

Backup dumps and row counts import the same exported PostgreSQL snapshot, even
while background workers commit. Web writes remain paused during media capture.
This does not replace offsite backup storage or scheduled restore exercises.

Runtime dependencies are hash locked in `requirements.lock`. CI builds the release
image, runs the PostgreSQL suite inside it, audits dependencies, and publishes the
tested main image to GHCR only after checks pass. The release-image-digest artifact
contains the immutable image reference. Configure `HUB_IMAGE` in the production
environment to that `ghcr.io/maximad/hub@sha256:...` value, and grant the deployment
host GHCR read access if the package is private. The deployment verifies the image
revision matches its target Git commit and checks its baked static manifest before
replacing running containers. There is no shared static volume to clear during
cutover. Build/test/publish must finish before deploying the new commit.

This change prepares a release; it does not deploy or change the production host.
