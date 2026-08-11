# Resetting experimental pre-launch data

`reset_prelaunch_data` is an allowlist-based, transactional management command.
It never flushes the database and never disables constraints. Its default dry-run
prints counts in child-to-parent dependency order and makes no writes:

```sh
python manage.py reset_prelaunch_data
```

## Deleted records (in order)

The command deletes these model tables: `NotificationRecipient`,
`NotificationLog`, `NotificationEvent`, `InternetRevenueShareAdjustment`,
`InternetRevenueShare`, `InternetAccessDevice`, `InternetSession`,
`InternetEntitlement`, `MemberCreditLedger`, `MemberActivationToken`,
`MemberDeviceToken`, `MembershipSubscription`, `Reservation`,
`VendorParticipation`, `EventMedia`, `EventTicketType`, `Event`,
`CurrencyEntrySnapshot`, `AuditEvent`, `ActivityLog`,
`PostingReconciliationFailure`, `FinanceReviewItem`,
`FinanceReconciliationState`, `PostingEntry`, `PostingCommand`,
`PurchasePayment`, `PurchaseReturnLine`, `StockMovement`, `PurchaseReturn`,
`PurchaseReceiptLine`, `PurchaseReceipt`, `PurchaseItem`, `Purchase`,
`ProductionBatchIngredient`, `ProductionBatch`, `CashMovement`, `Transfer`,
`DailyCloseRevision`, `DailyClose`, `Expense`, `OrderDiscount`, `Payment`,
`OrderItem`, `Order`, `PostingBatch`, and `Shift`.

## Preserved records

The command preserves migrations; users, groups, roles, and permissions; member
profiles; notification preferences; rooms, tables/areas, and QR/public codes;
all catalog/menu/category/product/option/tag/recipe records; inventory items;
expense categories; financial accounts; membership plans and benefit rules;
internet packages/profiles/partners/users and Wi-Fi networks; system and page
settings; **all `ExchangeRate` records**; vendors and vendor media associations;
all `MediaAsset` rows; and every file under the media root.

## Execution procedure

First produce a successful backup using `scripts/backup-production.sh`. The
command requires its non-empty version-1 manifest to be no more than 24 hours
old, checks artifact sizes, the success marker, and every SHA-256 checksum.

```sh
python manage.py reset_prelaunch_data \
  --execute \
  --confirmation "RESET EXPERIMENTAL DATA" \
  --backup-manifest /opt/hub/backups/production/hub-YYYYMMDDTHHMMSSZ/manifest.txt \
  --production-approved
```

`--production-approved` is mandatory whenever `DEBUG=False`. Deletion and
sequence resets run inside one `transaction.atomic()` block; failed preservation,
GenericForeignKey, orphan, or reconciliation checks roll everything back. A
successful run prints before/after reports. A second run is safe and reports zero
operational rows. Run `python manage.py system_audit` and
`python manage.py smoke_check` immediately afterward.
