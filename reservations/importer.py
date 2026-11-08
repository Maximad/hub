"""Private event booking CSV onboarding: atomic, idempotent, preview first.

Only aggregate results and row numbers may be logged or printed. Guest names and
phones are never included in errors, audit metadata, GitHub or Notion.
"""
import csv
import io
import re
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from accounts.permissions import is_owner_or_admin
from core.models import ActivityLog
from events.models import Event
from .models import Reservation, ReservationImportRecord
from .services import create_reservation, event_reserved_party_size
from .ticketing import set_event_ticket_allocations

HEADERS = ('event_uuid', 'reference', 'name', 'phone', 'party_size', 'status', 'tickets')
SOURCE_RE = re.compile(r'^[a-zA-Z0-9_.:-]{1,40}$')
REFERENCE_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9_.:-]{0,79}$')
PHONE_RE = re.compile(r'^[\d\s+()\-]{5,30}$')
MAX_ROWS = 500


class BookingImportError(Exception):
    """Only sanitized row numbers/codes are permitted in operator output."""

    def __init__(self, row, code):
        self.row = row
        self.code = code
        super().__init__(f'Row {row}: {code}')


def _parse_tiers(raw, row_number):
    result = {}
    if not (raw or '').strip():
        return result
    for fragment in raw.split(';'):
        pair = fragment.strip().split(':')
        if len(pair) != 2:
            raise BookingImportError(row_number, 'ticket_format')
        try:
            ticket_id, quantity = int(pair[0]), int(pair[1])
        except (ValueError, OverflowError):
            raise BookingImportError(row_number, 'ticket_format')
        if ticket_id <= 0 or quantity <= 0 or ticket_id in result:
            raise BookingImportError(row_number, 'ticket_quantity_or_duplicate')
        result[ticket_id] = quantity
    return result


def _read_rows(text):
    try:
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None or any(h not in reader.fieldnames for h in HEADERS):
            raise BookingImportError(1, 'required_headers_missing')
        if any(not h or h not in HEADERS for h in reader.fieldnames):
            raise BookingImportError(1, 'unknown_headers')
        rows = []
        seen = set()
        for row_number, raw in enumerate(reader, 2):
            if len(rows) >= MAX_ROWS:
                raise BookingImportError(row_number, 'too_many_rows')
            if None in raw:
                raise BookingImportError(row_number, 'extra_csv_columns')
            try:
                event_uuid = UUID((raw['event_uuid'] or '').strip())
                qty = int((raw['party_size'] or '').strip())
            except (TypeError, ValueError, AttributeError):
                raise BookingImportError(row_number, 'invalid_event_or_party')
            ref = (raw['reference'] or '').strip()
            name = (raw['name'] or '').strip()
            phone = (raw['phone'] or '').strip()
            status = (raw['status'] or '').strip()
            if not REFERENCE_RE.fullmatch(ref):
                raise BookingImportError(row_number, 'invalid_reference')
            if not 1 <= len(name) <= 120 or not PHONE_RE.fullmatch(phone):
                raise BookingImportError(row_number, 'invalid_guest_fields')
            if not 1 <= qty <= 50:
                raise BookingImportError(row_number, 'invalid_party_size')
            if status not in {Reservation.Status.PENDING, Reservation.Status.CONFIRMED}:
                raise BookingImportError(row_number, 'invalid_status')
            tiers = _parse_tiers(raw.get('tickets'), row_number)
            if tiers and sum(tiers.values()) != qty:
                raise BookingImportError(row_number, 'tier_total_mismatch')
            key = (event_uuid, ref)
            if key in seen:
                raise BookingImportError(row_number, 'duplicate_reference_in_file')
            seen.add(key)
            rows.append({
                'line': row_number, 'event_uuid': event_uuid, 'reference': ref,
                'name': name, 'phone': phone, 'party_size': qty,
                'status': status, 'tickets': tiers,
            })
        return rows
    except csv.Error:
        raise BookingImportError(1, 'invalid_csv')


def import_booking_csv(text, *, source, actor, apply=False):
    if not is_owner_or_admin(actor):
        raise ValidationError({'actor': 'يتطلب استيراد الحجوزات صلاحية الإدارة.'})
    if not SOURCE_RE.fullmatch(source or ''):
        raise BookingImportError(1, 'invalid_source')
    if len(text.encode('utf-8')) > 1_000_000:
        raise BookingImportError(1, 'file_too_large')
    rows = _read_rows(text)
    outcome = {'rows': len(rows), 'created': 0, 'skipped': 0, 'unclassified': 0, 'applied': bool(apply)}

    with transaction.atomic():
        for row in rows:
            # A single Event lock serializes all imports of the same show with
            # booking confirmation, ticket tier allocation and other imports.
            event = Event.objects.select_for_update().filter(uuid=row['event_uuid']).first()
            if not event:
                raise BookingImportError(row['line'], 'event_not_found')
            if event.status in {Event.Status.CANCELLED, Event.Status.COMPLETED}:
                raise BookingImportError(row['line'], 'event_closed')
            existing = (
                ReservationImportRecord.objects.select_related('reservation')
                .filter(event=event, source=source, external_key=row['reference'])
                .first()
            )
            if existing:
                booking = existing.reservation
                if (
                    booking.event_id != event.pk
                    or booking.name != row['name']
                    or booking.phone != row['phone']
                    or booking.party_size != row['party_size']
                ):
                    raise BookingImportError(row['line'], 'imported_reference_conflict')
                if row['tickets']:
                    allocations = {
                        a.ticket_type_id: a.quantity
                        for a in booking.ticket_allocations.all()
                    }
                    if allocations != row['tickets']:
                        raise BookingImportError(row['line'], 'imported_ticket_conflict')
                outcome['skipped'] += 1
                continue
            if Reservation.objects.filter(
                event=event, name=row['name'], phone=row['phone'],
                party_size=row['party_size'],
            ).exists():
                raise BookingImportError(row['line'], 'possible_existing_reservation')
            if event.capacity is not None:
                active = event_reserved_party_size(event.pk)
                if active + row['party_size'] > event.capacity:
                    raise BookingImportError(row['line'], 'event_capacity_exceeded')
            try:
                booking = create_reservation(Reservation(
                    reservation_type=Reservation.ReservationType.EVENT,
                    event=event,
                    name=row['name'],
                    phone=row['phone'],
                    party_size=row['party_size'],
                    status=row['status'],
                    created_by=actor,
                ))
                if row['tickets']:
                    set_event_ticket_allocations(
                        booking.pk, row['tickets'], actor=actor,
                    )
                else:
                    outcome['unclassified'] += 1
                ReservationImportRecord.objects.create(
                    event=event, source=source, external_key=row['reference'],
                    reservation=booking, imported_by=actor,
                )
                ActivityLog.objects.create(
                    actor=actor, action='reservation.imported',
                    details={'reservation_id': booking.pk, 'event_id': event.pk,
                             'source': source},
                )
            except ValidationError:
                raise BookingImportError(row['line'], 'booking_or_capacity_validation_failed')
            outcome['created'] += 1
        if not apply:
            transaction.set_rollback(True)
    return outcome
