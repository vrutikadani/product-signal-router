"""Signal records, lifecycle and seller feedback.

The CRM is the system of record for the seller's disposition; this store is the
engine's own memory of what it surfaced and what came back. In production it is
a table (or a custom CRM object) with one row per signal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from .models import Evaluation

OPEN = "OPEN"
ACTIONED = "ACTIONED"
SNOOZED = "SNOOZED"
DISMISSED = "DISMISSED"
EXPIRED = "EXPIRED"

DISMISS_REASONS = {"not_a_fit", "already_engaged", "bad_data", "wrong_owner", "bad_timing"}


@dataclass
class SignalRecord:
    key: str
    account_id: str
    as_of: date
    logic_version: str
    strength: str
    confidence: str
    crm_account_id: Optional[str] = None
    owner_id: Optional[str] = None
    status: str = OPEN
    snoozed_until: Optional[date] = None
    dismiss_reason: Optional[str] = None
    history: list[tuple[str, str]] = field(default_factory=list)


class SignalStore:
    def __init__(self) -> None:
        self.records: dict[str, SignalRecord] = {}

    @staticmethod
    def key_for(ev: Evaluation) -> str:
        # Keyed on the day the features describe, not the time the job ran, and
        # not on logic version: a re-run under new logic must not open a second
        # signal for the same account-day.
        return f"{ev.account_id}:{ev.as_of.isoformat()}"

    def record_surfaced(self, ev: Evaluation) -> tuple[SignalRecord, bool]:
        """Idempotent: the same evaluation recorded twice returns the existing row."""
        key = self.key_for(ev)
        if key in self.records:
            return self.records[key], False
        rec = SignalRecord(key, ev.account_id, ev.as_of, ev.logic_version,
                           ev.strength.value, ev.confidence.value, ev.crm_account_id, ev.owner_id)
        rec.history.append((ev.as_of.isoformat(), "SURFACED"))
        self.records[key] = rec
        return rec, True

    def for_account(self, account_id: str, crm_account_id: Optional[str] = None) -> list[SignalRecord]:
        return sorted((r for r in self.records.values()
                       if r.account_id == account_id
                       or (crm_account_id is not None and r.crm_account_id == crm_account_id)),
                      key=lambda r: r.as_of)

    def last_surfaced(self, account_id: str, before: Optional[date] = None,
                      crm_account_id: Optional[str] = None) -> Optional[SignalRecord]:
        """Most recent signal for this product account or anything sharing its CRM account."""
        recs = [r for r in self.for_account(account_id, crm_account_id)
                if before is None or r.as_of < before]
        return recs[-1] if recs else None

    def delivered_to_owner(self, owner_id: str, start: date, end_exclusive: date) -> int:
        return sum(1 for r in self.records.values()
                   if r.owner_id == owner_id and start <= r.as_of < end_exclusive)

    def get(self, key: str) -> Optional[SignalRecord]:
        return self.records.get(key)

    # seller feedback -----------------------------------------------------
    def action(self, key: str, on: date) -> None:
        r = self.records[key]
        r.status = ACTIONED
        r.history.append((on.isoformat(), "ACTIONED"))

    def snooze(self, key: str, on: date, days: int) -> None:
        r = self.records[key]
        r.status = SNOOZED
        r.snoozed_until = on + timedelta(days=days)
        r.history.append((on.isoformat(), f"SNOOZED {days}d"))

    def dismiss(self, key: str, on: date, reason: str) -> None:
        if reason not in DISMISS_REASONS:
            raise ValueError(f"unknown dismiss reason {reason!r}")
        r = self.records[key]
        r.status = DISMISSED
        r.dismiss_reason = reason
        r.history.append((on.isoformat(), f"DISMISSED {reason}"))
