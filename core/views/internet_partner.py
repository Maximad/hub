"""Backward-compatible import for the former one-page partner portal."""
from core.views.internet_provider import legacy_internet_partner_dashboard as internet_partner_dashboard


__all__ = ['internet_partner_dashboard']
