"""Canonical operational audit trail — single history stream for all channels."""

from core.operational_audit.service import record_operational_audit_event

__all__ = ["record_operational_audit_event"]
