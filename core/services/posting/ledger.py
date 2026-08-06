from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.models import PostingBatch
from .exceptions import InvalidTransition


@transaction.atomic
def post_balanced_batch(batch):
    """Post a batch only after checking its entries while holding their parent lock."""
    locked = PostingBatch.objects.select_for_update().get(pk=batch.pk)
    totals = locked.entries.aggregate(debits=Sum('debit'), credits=Sum('credit'))
    debits = totals['debits'] or Decimal('0')
    credits = totals['credits'] or Decimal('0')
    if not locked.entries.exists():
        raise InvalidTransition('لا يمكن ترحيل قيد بلا أسطر.')
    if debits != credits:
        raise InvalidTransition('يجب أن يتساوى مجموع المدين والدائن قبل ترحيل القيد.')
    locked.status = PostingBatch.Status.POSTED
    locked.posted_at = timezone.now()
    locked.full_clean()
    locked.save(update_fields=['status', 'posted_at', 'updated_at'])
    return locked
