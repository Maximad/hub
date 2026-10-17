# Hub Sweida public market-rate archive

Files written here are **unapproved external market observations**, not production accounting rates.

The scheduled workflow on `main` writes `rates/latest.json` and daily archives only after validation. Hub Suite imports candidates for **manual superuser approval**. Missing data must never be silently replaced by fabricated values.

No API credentials or local Sweida adjustment settings belong on this branch.
