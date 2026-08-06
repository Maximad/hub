from core.models import CashMovement
from .engine import dispatch
from .exceptions import InvalidTransition


def transfer(source_movement, context, amount, title, notes=''):
    """Create linked out/in movements; ``source_movement`` identifies the source account row."""
    def handle(source):
        if amount <= 0: raise InvalidTransition('مبلغ التحويل يجب أن يكون موجباً.')
        source.business_date=context.date_for(source); source.movement_type=CashMovement.MovementType.CASH_WITHDRAWAL; source.direction=CashMovement.Direction.OUT; source.amount_syp=amount; source.title=title; source.notes=notes; source.created_by=context.actor; source.full_clean(); source.save()
        CashMovement.objects.create(business_date=source.business_date,movement_type=CashMovement.MovementType.CASH_DEPOSIT,direction=CashMovement.Direction.IN,amount_syp=amount,title=title,notes=f'linked:{source.pk} {notes}',created_by=context.actor,approved_by=context.approver)
        return source
    return dispatch('account.transfer',source_movement,context,handle)
