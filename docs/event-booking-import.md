# Import existing event reservations (private operator runbook)

This imports bookings already received by Hub staff; it does **not** publish events, collect cash, redeem drinks, or infer a paid ticket from a WhatsApp message.

## CSV schema

Exactly these UTF-8 columns (no extra columns):

```csv
event_uuid,reference,name,phone,party_size,status,tickets
11111111-1111-4111-8111-111111111111,BK001,Example Guest,+963900000001,3,confirmed,12:2;13:1
```

- `event_uuid`: existing event UUID. Verify the correct show and performance time before importing.
- `reference`: unique **non-personal**, stable identifier, beginning with a letter (for example `BK001`); never use a phone number or private name.
- `name`, `phone`: booking contact. Keep this CSV private, outside GitHub/Notion.
- `party_size`: integer from 1 through 50, subject to actual safe approved capacity.
- `status`: `pending` or `confirmed`. No payment inference.
- `tickets`: optional, semicolon-separated `ticket_type_id:quantity`. If supplied, quantities must equal `party_size`, tier IDs must belong to that event, and capacity is enforced. Blank allows an existing unclassified booking to be entered for later review.

Example IDs/numbers above are fake and must be replaced with actual approved event/tier IDs.

## Preview by default; explicit apply

Use a secure private path accessible to the web container, for example copy a local CSV to the running container with `docker cp`. Do not put private CSVs in the repository, shared media, public web root, or GitHub issue.

```bash
docker compose -f docker-compose.prod.yml --env-file .env \
  exec web python manage.py import_event_bookings \
  --file /tmp/private-bookings.csv \
  --source whatsapp-20261121 \
  --actor YOUR_ADMIN_USERNAME
```

The preview executes the same database validations in one transaction and rolls back all writes. Output gives only aggregate row totals or sanitized row numbers/errors, not personal guest details.

If preview is clean, verify production backup and then run explicitly:

```bash
docker compose -f docker-compose.prod.yml --env-file .env \
  exec web python manage.py import_event_bookings \
  --file /tmp/private-bookings.csv \
  --source whatsapp-20261121 \
  --actor YOUR_ADMIN_USERNAME \
  --apply --expected-rows 10
```

Replace 10 with the actual validated row count. If this number differs, nothing is written. Delete the private CSV from the container after completing approved intake.

## Safety guarantees

- Whole-file transactional apply; a single invalid booking/tier/capacity aborts the import.
- Idempotent by `(event, source, reference)`; repeating the same file skips existing imports.
- Suspicious duplicates against pre-existing manual bookings stop for operator review.
- Imports are serialized on the event record, using existing reservation and tier-capacity validation.
- Imported record stores only source/reference linkage; guest contact stays solely in the protected reservation.
- No refunds, payments, POS orders, stock movements, or external messages are created.
- Changes to existing imported names/phones/tickets are deliberately rejected rather than silently overwritten.

## Remaining limitations

This is an administrator-only server import, not yet a polished upload/preview interface for regular staff. Staff still need to classify bookings with no ticket tier and reconcile actual cashier payment receipts separately. Physical venue capacity must be verified independently.
