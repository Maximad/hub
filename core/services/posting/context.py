from dataclasses import dataclass, field
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
        return self.business_date or getattr(source, 'business_date', None) or timezone.localdate()
