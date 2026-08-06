from core.models import CashMovement, Payment, PostingCommand, PostingReconciliationFailure, StockMovement


def record_unsupported_bypasses():
    """Persist one failure for every generated record not covered by a command receipt.

    This deliberately makes legacy/bulk writes visible; bulk operations do not run
    model signals and therefore must never be treated as a posting mechanism.
    """
    covered={(r.result_type,r.result_id) for r in PostingCommand.objects.exclude(result_id='')}
    failures=[]
    for model in (Payment,CashMovement,StockMovement):
        label=model._meta.label
        for record in model.objects.all().iterator():
            pk=record.pk
            linked = (
                isinstance(record, CashMovement) and (
                    record.related_payment_id and ('core.Payment', str(record.related_payment_id)) in covered or
                    record.related_expense_id and ('core.Expense', str(record.related_expense_id)) in covered
                ) or
                isinstance(record, StockMovement) and record.related_purchase_id and
                ('core.Purchase', str(record.related_purchase_id)) in covered
            )
            if (label,str(pk)) not in covered and not linked:
                failure,_=PostingReconciliationFailure.objects.get_or_create(record_type=label,record_id=str(pk),defaults={'reason':'Unsupported direct write bypassed core.services.posting'})
                failures.append(failure)
    return failures
