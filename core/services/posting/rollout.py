"""Runtime policy for the additive ledger rollout.

Deployment settings override the database switches when present.  This gives an
operator an emergency kill switch even when the admin/database is unavailable.
Setting ``POSTING_LEDGER_WRITES_ENABLED`` false always implies legacy reads.
"""
from dataclasses import dataclass

from django.conf import settings

from core.models import PostingReconciliationFailure, SystemSetting


class RolloutBlocked(RuntimeError):
    """A later rollout phase was requested before its safety gate passed."""


@dataclass(frozen=True)
class PostingRollout:
    ledger_writes: bool
    dual_reads: bool
    report_reads: bool


def _configured(name, database_value):
    return bool(getattr(settings, name, database_value))


def current_rollout():
    row = SystemSetting.objects.order_by('-updated_at', '-pk').first() or SystemSetting()
    writes = _configured('POSTING_LEDGER_WRITES_ENABLED', row.posting_ledger_writes_enabled)
    # The write kill switch is intentionally also the one-step read rollback.
    dual = writes and _configured('POSTING_DUAL_READ_ENABLED', row.posting_dual_read_enabled)
    reports = dual and _configured('POSTING_REPORT_READS_ENABLED', row.posting_reports_enabled)
    return PostingRollout(writes, dual, reports)


def assert_phase_can_enable(phase):
    """Enforce ordered activation and a clean reconciliation boundary."""
    policy = current_rollout()
    if phase == 'dual_reads' and not policy.ledger_writes:
        raise RolloutBlocked('Dual reads require ledger writes to be enabled.')
    if phase == 'report_reads' and not policy.dual_reads:
        raise RolloutBlocked('Report cutover requires dual-read comparison first.')
    if phase in {'dual_reads', 'report_reads'} and PostingReconciliationFailure.objects.filter(resolved_at__isnull=True).exists():
        raise RolloutBlocked('Unresolved critical posting reconciliation discrepancies remain.')
