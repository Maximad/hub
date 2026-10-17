"""Collect a strictly validated, *unapproved* USD market rate from LiraScope.

This is an independent standard-library script run by GitHub Actions.
It never contacts Hub production, and never approves a financial conversion.
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


API_URL = "https://lirascope.syria-cloud.sy/api/v1/rates/latest?currencies=USD&lang=en"
DAMASCUS = ZoneInfo("Asia/Damascus")
MAX_AGE = timedelta(hours=36)
MAX_BYTES = 65536


class RateFeedError(ValueError):
    pass


def decimal_rate(raw):
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise RateFeedError("Missing or invalid exchange-rate number") from exc
    if not value.is_finite() or not (Decimal("1") <= value <= Decimal("1000")):
        raise RateFeedError("USD exchange rate outside the conservative new-SYP range")
    return value


def utc_instant(raw):
    if not isinstance(raw, str) or not raw:
        raise RateFeedError("Missing source rate timestamp")
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RateFeedError("Invalid source timestamp") from exc
    if value.tzinfo is None:
        raise RateFeedError("Source timestamp must include a timezone")
    return value.astimezone(timezone.utc)


def validate_payload(payload, raw_bytes, *, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not isinstance(payload, dict):
        raise RateFeedError("Expected an object from the API")
    meta = payload.get("meta") or {}
    denomination = meta.get("denomination") if isinstance(meta, dict) else None
    if denomination and str(denomination).lower() not in {"new", "syp_new", "new_syp"}:
        raise RateFeedError("Feed explicitly reports an unsupported SYP denomination")
    entries = payload.get("marketRates")
    if not isinstance(entries, list):
        raise RateFeedError("Missing marketRates (central-bank rates are not a substitute)")
    usd = [x for x in entries if isinstance(x, dict) and x.get("currency") == "USD"]
    if len(usd) != 1:
        raise RateFeedError("Expected exactly one USD market rate")
    item = usd[0]
    if item.get("isManualOverride"):
        raise RateFeedError("Manual source override requires investigation")
    buy, sell = decimal_rate(item.get("buy")), decimal_rate(item.get("sell"))
    if buy > sell or sell > buy * Decimal("1.15"):
        raise RateFeedError("Implausible buying/selling spread")
    at = utc_instant(item.get("timestampUtc"))
    if at > now + timedelta(minutes=15) or now - at > MAX_AGE:
        raise RateFeedError("Source rate is stale or from the future")
    return {
        "schema_version": 1,
        "provider": "lirascope",
        "reference_market": "marketRates",
        "city": "unspecified",
        "currency": "USD",
        "denomination": "SYP_NEW",
        "denomination_verification": "numeric_safety_bounds_and_manual_approval",
        "buy_syp_per_usd": str(buy),
        "sell_syp_per_usd": str(sell),
        "source_timestamp_utc": at.isoformat().replace("+00:00", "Z"),
        "business_date_damascus": at.astimezone(DAMASCUS).date().isoformat(),
        "collected_at_utc": now.isoformat().replace("+00:00", "Z"),
        "source_url": API_URL,
        "raw_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "status": "unapproved",
    }


def fetch():
    request = Request(API_URL, headers={"Accept": "application/json", "User-Agent": "HubSweidaExchangeRateCollector/1"})
    with urlopen(request, timeout=15) as reply:
        if "application/json" not in reply.headers.get("Content-Type", "").lower():
            raise RateFeedError("Source did not return JSON")
        raw = reply.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise RateFeedError("Unexpectedly large source response")
    try:
        return json.loads(raw), raw
    except (ValueError, UnicodeDecodeError) as exc:
        raise RateFeedError("Malformed source JSON") from exc


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload, raw = fetch()
        record = validate_payload(payload, raw)
    except (RateFeedError, OSError, ValueError) as exc:
        print("Rate collection REJECTED: " + str(exc), file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Rate candidate collected (NOT approved): " + record["business_date_damascus"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
