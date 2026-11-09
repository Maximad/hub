from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Mapping, Optional

from django.utils import timezone


@dataclass(frozen=True)
class PostingContext:
    actor: Any = None
    approver: Any = None
    business_date: Optional[date] = None
    idempotency_key: str = ''
    channel: str = 'application'
    request_metadata: Mapping[str, Any] = field(default_factory=dict)

    def date_for(self, source=None):
        from core.finance import current_business_date
        return self.business_date or getattr(source, 'business_date', None) or current_business_date()

    def with_key_suffix(self, suffix):
        """Derive a stable key for one stage of a multi-command request."""
        return replace(self, idempotency_key=f'{self.idempotency_key}:{suffix}')

