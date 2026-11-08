import uuid
from django.db import models
from django.db import transaction
from django.core.exceptions import ValidationError
from django.apps import apps


class Reservation(models.Model):
    class ReservationType(models.TextChoices):
        REGULAR = 'regular', 'حجز عادي'
        EVENT = 'event', 'حجز فعالية'

    class Status(models.TextChoices):
        PENDING = 'pending', 'بانتظار التأكيد'
        CONFIRMED = 'confirmed', 'مؤكد'
        CANCELLED = 'cancelled', 'ملغى'
        COMPLETED = 'completed', 'منتهٍ'
        NO_SHOW = 'no_show', 'لم يحضر'

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    reservation_type = models.CharField(max_length=30, choices=ReservationType.choices, default=ReservationType.REGULAR)
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=30)
    table_area = models.ForeignKey('core.TableArea', on_delete=models.SET_NULL, null=True, blank=True)
    room = models.ForeignKey('core.Room', on_delete=models.SET_NULL, null=True, blank=True, related_name='reservations')
    event = models.ForeignKey('events.Event', on_delete=models.SET_NULL, null=True, blank=True)
    visit = models.OneToOneField(
        'core.HubVisit',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reservation',
    )
    reservation_date = models.DateField(null=True, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    party_size = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    deposit_syp = models.IntegerField(default=0)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(
                fields=['reservation_date', 'start_time', 'end_time', 'status'],
                name='reservation_interval_idx',
            ),
            models.Index(
                fields=['event', 'status'],
                name='reservation_event_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(end_time__isnull=True)
                    | models.Q(start_time__isnull=True)
                    | models.Q(end_time__gt=models.F('start_time'))
                ),
                name='reservation_end_after_start',
            ),
        ]

    TRANSITIONS = {
        Status.PENDING: frozenset({Status.CONFIRMED, Status.CANCELLED}),
        Status.CONFIRMED: frozenset({Status.COMPLETED, Status.NO_SHOW, Status.CANCELLED}),
        Status.CANCELLED: frozenset(),
        Status.COMPLETED: frozenset(),
        Status.NO_SHOW: frozenset(),
    }
    CORRECTIONS = {
        Status.CANCELLED: frozenset({Status.PENDING, Status.CONFIRMED}),
        Status.COMPLETED: frozenset({Status.PENDING, Status.CONFIRMED}),
        Status.NO_SHOW: frozenset({Status.PENDING, Status.CONFIRMED}),
    }

    @classmethod
    def transition_status(cls, reservation_id, *, actor, new_status):
        return cls._change_status(reservation_id, actor=actor, new_status=new_status, reason='', correction=False)

    @classmethod
    def correct_status(cls, reservation_id, *, actor, new_status, reason):
        return cls._change_status(reservation_id, actor=actor, new_status=new_status, reason=reason, correction=True)

    @classmethod
    def _change_status(cls, reservation_id, *, actor, new_status, reason, correction):
        """Validate and persist a status change while holding the reservation row lock."""
        with transaction.atomic():
            reservation = (
                cls.objects.select_for_update(of=('self',))
                .select_related('visit')
                .get(pk=reservation_id)
            )
            old_status = reservation.status
            if (reservation.reservation_type == cls.ReservationType.EVENT
                    and new_status in {cls.Status.NO_SHOW, cls.Status.CANCELLED}
                    and EventAdmission.objects.filter(reservation_id=reservation.pk).exists()):
                raise ValidationError({'status': 'تم تسجيل وصول ضيوف لهذا الحجز؛ لا يمكن اعتباره غياباً أو إلغاءه.'})
            if reservation.visit_id and reservation.visit.status == 'open':
                raise ValidationError({'status': 'لا يمكن تغيير حالة الحجز أثناء جلسة مفتوحة. أغلق الجلسة المرتبطة أولاً.'})
            allowed = cls.CORRECTIONS.get(old_status, ()) if correction else cls.TRANSITIONS.get(old_status, ())
            if new_status not in allowed:
                raise ValidationError({'status': f'Transition from {old_status} to {new_status} is not permitted.'})
            if correction:
                if not (getattr(actor, 'is_active', False) and (getattr(actor, 'is_superuser', False) or getattr(actor, 'role', '') == 'admin')):
                    raise ValidationError({'status': 'Only an administrator may correct a terminal reservation.'})
                if not (reason or '').strip():
                    raise ValidationError({'reason': 'A reason is required for a reservation correction.'})
            reservation.status = new_status
            # Status changes can make a reservation active again.  Re-run the
            # domain availability/capacity checks under the same transaction
            # and row/resource locks before persisting it.
            from .services import _validate_for_status
            _validate_for_status(reservation)
            reservation.save(update_fields=['status', 'end_time', 'room', 'updated_at'])
            AuditEvent = apps.get_model('core', 'AuditEvent')
            AuditEvent.objects.create(
                actor=actor, action='reservation_status_correction' if correction else 'reservation_status_transition',
                source=reservation, before_snapshot={'status': old_status},
                after_snapshot={'status': new_status, 'reason': (reason or '').strip()}, channel='staff',
            )
            return reservation

    @property
    def allowed_status_transitions(self):
        return self.TRANSITIONS.get(self.status, frozenset())

    @property
    def operational_status_label(self):
        if self.visit_id:
            if self.visit.status == 'open':
                return 'داخل الجلسة'
            return 'انتهت الجلسة'
        labels = {
            self.Status.PENDING: 'بانتظار التأكيد',
            self.Status.CONFIRMED: 'مؤكد — بانتظار الوصول',
            self.Status.CANCELLED: 'ملغى',
            self.Status.COMPLETED: 'منتهٍ',
            self.Status.NO_SHOW: 'لم يحضر',
        }
        return labels.get(self.status, self.get_status_display())

    def __str__(self):
        return f'{self.name} — {self.effective_date or "—"} {self.effective_starts_at or "—"} — {self.phone}'

    @property
    def effective_date(self):
        return self.event.starts_at.date() if self.reservation_type == self.ReservationType.EVENT and self.event_id else self.reservation_date

    @property
    def effective_starts_at(self):
        return self.event.starts_at.time() if self.reservation_type == self.ReservationType.EVENT and self.event_id else self.start_time

    @property
    def effective_ends_at(self):
        if self.reservation_type == self.ReservationType.EVENT and self.event_id:
            return self.event.ends_at.time() if self.event.ends_at else None
        return self.end_time

    @property
    def effective_room(self):
        if self.reservation_type == self.ReservationType.EVENT and self.event_id:
            return self.event.room
        return self.room or (self.table_area.room if self.table_area_id else None)

    def clean(self):
        errors = {}
        if self.reservation_type == self.ReservationType.EVENT:
            if not self.event_id:
                errors['event'] = 'يرجى اختيار الفعالية.'
            self.reservation_date = self.start_time = self.end_time = None
            self.room = self.table_area = None
        else:
            if self.event_id:
                errors['event'] = 'لا يمكن ربط حجز عادي بفعالية.'
            if not self.reservation_date:
                errors['reservation_date'] = 'يرجى تحديد تاريخ الحجز.'
            if not self.start_time:
                errors['start_time'] = 'يرجى تحديد وقت بدء الحجز.'
            if self.start_time and self.end_time and self.end_time <= self.start_time:
                errors['end_time'] = 'وقت النهاية يجب أن يكون بعد وقت البداية.'
            if self.table_area_id and self.room_id and self.table_area.room_id != self.room_id:
                errors['table_area'] = 'الطاولة المختارة لا تتبع للمساحة المحددة.'
        if errors:
            raise ValidationError(errors)


class EventAttendanceRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'بانتظار مراجعة الفريق'
        APPROVED = 'approved', 'اعتمده الفريق'
        REJECTED = 'rejected', 'لم يعتمد الفريق الطلب'
        WITHDRAWN = 'withdrawn', 'سحب الزائر الطلب'

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    client_key = models.UUIDField(unique=True, editable=False)
    owner_hash = models.CharField(max_length=64, editable=False)
    ip_hash = models.CharField(max_length=64, editable=False)
    event = models.ForeignKey('events.Event', null=True, on_delete=models.SET_NULL, related_name='attendance_requests')
    event_uuid = models.UUIDField(editable=False)
    event_title = models.CharField(max_length=200)
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=30)
    party_size = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    reservation = models.OneToOneField(Reservation, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name='attendance_request')
    reviewed_by = models.ForeignKey('accounts.User', null=True, blank=True, on_delete=models.SET_NULL)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=['owner_hash', 'created_at']), models.Index(fields=['ip_hash', 'created_at']),
                   models.Index(fields=['status', 'created_at'])]
        constraints = [
            models.CheckConstraint(condition=models.Q(party_size__gt=0), name='attendance_request_positive_party'),
            models.UniqueConstraint(fields=['event', 'owner_hash'], condition=models.Q(status__in=['pending', 'approved']),
                                    name='one_open_attendance_request_per_browser'),
        ]


class ReservationTicketAllocation(models.Model):
    """Quantity and immutable booking-price snapshot for one event ticket tier.

    This is an allocation, not a paid sale. Real receipts belong to the finance
    posting engine. Historical bookings may have no allocations until reviewed.
    """
    reservation = models.ForeignKey(
        Reservation, on_delete=models.CASCADE, related_name='ticket_allocations',
    )
    ticket_type = models.ForeignKey(
        'events.EventTicketType', on_delete=models.PROTECT, related_name='reservation_allocations',
    )
    quantity = models.PositiveIntegerField()
    unit_price_syp = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('reservation', 'ticket_type'),
                name='unique_reservation_ticket_tier',
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name='reservation_ticket_quantity_positive',
            ),
        ]

    def clean(self):
        super().clean()
        if self.reservation_id and self.ticket_type_id:
            if (self.reservation.reservation_type != Reservation.ReservationType.EVENT
                    or self.reservation.event_id != self.ticket_type.event_id):
                raise ValidationError({'ticket_type': 'فئة التذكرة لا تتبع فعالية هذا الحجز.'})


class ReservationPaymentArrangement(models.Model):
    """Booking collection arrangement, deliberately separate from posted payments.

    This record never means a ticket was paid; a future reconciliation must link
    actual POS/finance postings before displaying financial settlement status.
    """
    class Mode(models.TextChoices):
        PAY_AT_DOOR = 'pay_at_door', 'الدفع عند الدخول'
        STAFF_GUARANTEE = 'staff_guarantee', 'مؤكد بضمان الإدارة'

    reservation = models.OneToOneField(
        Reservation, on_delete=models.CASCADE, related_name='payment_arrangement',
    )
    mode = models.CharField(max_length=24, choices=Mode.choices)
    guaranteed_by = models.ForeignKey(
        'accounts.User', on_delete=models.PROTECT, null=True, blank=True,
        related_name='guaranteed_reservations',
    )
    approved_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='approved_reservation_arrangements',
    )
    due_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=250, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(mode='pay_at_door', guaranteed_by__isnull=True)
                    | models.Q(mode='staff_guarantee', guaranteed_by__isnull=False)
                ),
                name='reservation_arrangement_guarantee_consistent',
            ),
        ]

    def clean(self):
        super().clean()
        if self.mode == self.Mode.STAFF_GUARANTEE:
            if not self.guaranteed_by_id:
                raise ValidationError({'guaranteed_by': 'اسم الضامن مطلوب.'})
            if len((self.reason or '').strip()) < 3:
                raise ValidationError({'reason': 'اكتب سبباً مختصراً لاعتماد الحجز بالضمان.'})
        elif self.mode == self.Mode.PAY_AT_DOOR and self.guaranteed_by_id:
            raise ValidationError({'guaranteed_by': 'الدفع عند الدخول لا يحتاج إلى ضامن.'})


class EventAdmission(models.Model):
    """Ticket admission for an event booking; never creates a hospitality visit."""
    reservation = models.OneToOneField(
        Reservation, on_delete=models.CASCADE, related_name='event_admission',
    )
    arrived_count = models.PositiveIntegerField()
    recorded_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='event_admissions_recorded',
    )
    first_recorded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(arrived_count__gt=0),
                name='event_admission_positive_attendees',
            ),
        ]

    def clean(self):
        super().clean()
        if self.reservation_id:
            if self.reservation.reservation_type != Reservation.ReservationType.EVENT:
                raise ValidationError({'reservation': 'لا يمكن تسجيل دخول تذاكر لحجز عادي.'})
            if self.arrived_count > self.reservation.party_size:
                raise ValidationError({'arrived_count': 'عدد الواصلين يتجاوز عدد الضيوف في الحجز.'})


class ReservationTicketPaymentLink(models.Model):
    """Attribution of a verified cashier payment, never a new collection.

    Exactly one event reservation can claim an existing payment receipt.
    Reversals remain owned by the finance posting engine.
    """
    reservation = models.ForeignKey(
        Reservation, on_delete=models.PROTECT, related_name='ticket_payment_links',
    )
    payment = models.OneToOneField(
        'core.Payment', on_delete=models.PROTECT, related_name='event_ticket_booking_link',
    )
    linked_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='linked_ticket_payments',
    )
    linked_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()
        if self.reservation_id and self.reservation.reservation_type != Reservation.ReservationType.EVENT:
            raise ValidationError({'reservation': 'ربط قبض التذاكر متاح لحجوزات الفعاليات فقط.'})


class ReservationTicketSale(models.Model):
    """Link an event booking's immutable ticket-charge order to its reservation.

    Payment evidence remains exclusively on Order/Payment/PostingBatch. No
    drink product is selected or automatically consumed by this link.
    """
    reservation = models.OneToOneField(
        Reservation, on_delete=models.PROTECT, related_name='ticket_sale',
    )
    order = models.OneToOneField(
        'core.Order', on_delete=models.PROTECT, related_name='event_ticket_sale',
    )
    booked_total_syp = models.PositiveIntegerField()
    created_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='event_ticket_orders_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()
        if self.reservation_id and (
            self.reservation.reservation_type != Reservation.ReservationType.EVENT
            or not self.reservation.event_id
        ):
            raise ValidationError({'reservation': 'طلب التذاكر يتطلب حجز فعالية.'})

