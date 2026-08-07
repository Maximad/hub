# Currency safety and USD entry

The ledger and consolidated reports remain denominated in **new SYP**. `100 old
SYP = 1 new SYP`; old SYP is never an accepted currency code and is never
converted automatically.

`core.currency` is the mandatory boundary for manual monetary entry. It parses
Arabic and Western numerals with `Decimal`, selects a dated non-future rate,
converts USD, evaluates risk, enforces acknowledgement/manager permission, and
stores an immutable `CurrencyEntrySnapshot`. Generated totals and ledger entries
pass `manually_entered=False` and are not interactively blocked.

Defaults are in `config/settings.py`: warning 5,000, acknowledgement 10,000 and
manager review 50,000 new SYP, separately configurable by operation. Rate maximum
age is `CURRENCY_RATE_MAX_AGE_DAYS` (three days). The data migration publishes an
initial 1 USD = 130 new SYP rate effective 2025-01-01. Administrators add a new
rate in Django admin; historical rows cannot be edited. A correction is another
row and the old row's `superseded_by` link records the chain.

Transaction currency, settlement currency, account currency, original amount,
rate and reporting amount are separate snapshot facts. Same-currency USD/SYP and
USD obligations can therefore be preserved. Cross-currency cash transfers must
be performed by an authorized conversion workflow; ordinary transfer posting
must not pretend that the same numeric units balance two currencies.

The current posting ledger records base SYP value. It does not recognize realized
exchange gains/losses or periodically revalue open USD liabilities. Snapshots
preserve the original and settlement facts required to implement that later,
without recalculating historical postings.

Run `python manage.py reconcile_currency` for the read-only JSON-lines report.
Filters: `--date-from`, `--date-to`, `--record-type`, `--currency`, `--reason`.
