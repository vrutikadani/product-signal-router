"""Signal records, lifecycle and seller feedback.

The CRM is the system of record for the seller's disposition; this store is the
engine's own memory of what it surfaced and what came back. In production it is
a table (or a custom CRM object) with one row per signal. `SignalStore` keeps
the rows in memory (tests, demo, replay). `sqlite_store.SqliteSignalStore` keeps
them in a file and has the same interface.

A record has two separate states:
- `delivery_status`: did the seller ever receive it? A record is written as
  PENDING_DELIVERY before the sink is called and becomes DELIVERED only after
  the sink returns. Only DELIVERED records count as engine memory.
- `status`: what the seller did with it (OPEN until they respond).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Optional

from .models import Evaluation

# seller lifecycle
OPEN = "OPEN"
ACTIONED = "ACTIONED"
SNOOZED = "SNOOZED"
DISMISSED = "DISMISSED"
# Not assigned by the engine. A signal older than open_signal_expiry_days with
# no response is treated as expired by the actionability rules, but its status
# stays OPEN. The constant is kept for a store that writes expiry explicitly.
EXPIRED = "EXPIRED"
CLEARED = "CLEARED"

# delivery
PENDING_DELIVERY = "PENDING_DELIVERY"              # recorded; sink not yet called, or failed and will retry
DELIVERED = "DELIVERED"                            # the sink returned
DELIVERY_FAILED = "DELIVERY_FAILED"                # gave up: permanent error, or the one retry failed
NOT_DELIVERED_BACKFILL = "NOT_DELIVERED_BACKFILL"  # decided during a backfill; never sent
MAX_DELIVERY_ATTEMPTS = 2                          # the first try plus one retry on replay

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
    delivery_status: str = PENDING_DELIVERY
    delivery_attempts: int = 0
    delivery_error: Optional[str] = None


class BaseSignalStore:
    """Store logic shared by every backend. A backend implements `_get`,
    `_insert` (returns False if the key exists), `_update`, `_iter`, and the
    processed-day pair."""

    # backend hooks -------------------------------------------------------
    def _get(self, key: str) -> Optional[SignalRecord]: raise NotImplementedError
    def _insert(self, rec: SignalRecord) -> bool: raise NotImplementedError
    def _update(self, rec: SignalRecord) -> None: raise NotImplementedError
    def _iter(self, account_id: Optional[str] = None,
              crm_account_id: Optional[str] = None) -> Iterable[SignalRecord]: raise NotImplementedError
    def latest_processed_day(self) -> Optional[date]: raise NotImplementedError
    def mark_processed(self, as_of: date) -> None: raise NotImplementedError

    @staticmethod
    def key_for(ev: Evaluation) -> str:
        # Keyed on the day the features describe, not the time the job ran, and
        # not on logic version: a re-run under new logic must not open a second
        # signal for the same account-day. The sink gets this key too, as its
        # idempotency key.
        return f"{ev.account_id}:{ev.as_of.isoformat()}"

    def record_surfaced(self, ev: Evaluation, backfill: bool = False) -> tuple[SignalRecord, bool]:
        """Idempotent: the same evaluation recorded twice returns the existing row."""
        key = self.key_for(ev)
        rec = SignalRecord(key, ev.account_id, ev.as_of, ev.logic_version,
                           ev.strength.value, ev.confidence.value, ev.crm_account_id, ev.owner_id,
                           delivery_status=NOT_DELIVERED_BACKFILL if backfill else PENDING_DELIVERY)
        rec.history.append((ev.as_of.isoformat(), "RECORDED (backfill, not sent)" if backfill else "SURFACED"))
        if self._insert(rec):
            return rec, True
        return self._get(key), False

    # delivery ------------------------------------------------------------
    def mark_delivered(self, key: str, on: date) -> None:
        r = self._get(key)
        r.delivery_status, r.delivery_attempts, r.delivery_error = DELIVERED, r.delivery_attempts + 1, None
        r.history.append((on.isoformat(), "DELIVERED"))
        self._update(r)

    def mark_delivery_failed(self, key: str, on: date, error: str, permanent: bool = False) -> str:
        """Count a failed attempt. Returns the new delivery status."""
        r = self._get(key)
        r.delivery_attempts += 1
        r.delivery_error = error
        if permanent or r.delivery_attempts >= MAX_DELIVERY_ATTEMPTS:
            r.delivery_status = DELIVERY_FAILED
        r.history.append((on.isoformat(), f"DELIVERY ATTEMPT {r.delivery_attempts} FAILED: {error}"))
        self._update(r)
        return r.delivery_status

    # reads ---------------------------------------------------------------
    def get(self, key: str) -> Optional[SignalRecord]:
        return self._get(key)

    def all_records(self) -> list[SignalRecord]:
        return sorted(self._iter(), key=lambda r: (r.as_of, r.key))

    def for_account(self, account_id: str, crm_account_id: Optional[str] = None) -> list[SignalRecord]:
        return sorted(self._iter(account_id, crm_account_id), key=lambda r: (r.as_of, r.key))

    def last_delivered(self, account_id: str, crm_account_id: Optional[str] = None,
                       exclude_as_of: Optional[date] = None) -> Optional[SignalRecord]:
        """Engine memory: the latest delivered signal for this product account or
        anything sharing its CRM account, on any day except `exclude_as_of`.

        Later days count too, so backfilling an old day sees a dismissal made
        after it. The evaluated day itself is excluded: a same-day record is the
        result of this evaluation, not an input to it, so a replay gives the
        same answer. Undelivered records are ignored: the seller never saw them.
        """
        recs = [r for r in self.for_account(account_id, crm_account_id)
                if r.delivery_status == DELIVERED and r.as_of != exclude_as_of]
        return recs[-1] if recs else None

    def delivered_to_owner(self, owner_id: str, start: date, end_exclusive: date) -> int:
        return sum(1 for r in self._iter()
                   if r.owner_id == owner_id and r.delivery_status == DELIVERED
                   and start <= r.as_of < end_exclusive)

    # seller feedback -----------------------------------------------------
    def action(self, key: str, on: date) -> None:
        r = self._get(key)
        r.status = ACTIONED
        r.history.append((on.isoformat(), "ACTIONED"))
        self._update(r)

    def snooze(self, key: str, on: date, days: int) -> None:
        r = self._get(key)
        r.status = SNOOZED
        r.snoozed_until = on + timedelta(days=days)
        r.history.append((on.isoformat(), f"SNOOZED {days}d"))
        self._update(r)

    def clear(self, key: str, on: date) -> None:
        """RevOps reviewed a 'bad data' or 'wrong owner' dismissal and resolved it."""
        r = self._get(key)
        r.status = CLEARED
        r.history.append((on.isoformat(), "CLEARED by review"))
        self._update(r)

    def dismiss(self, key: str, on: date, reason: str) -> None:
        if reason not in DISMISS_REASONS:
            raise ValueError(f"unknown dismiss reason {reason!r}")
        r = self._get(key)
        r.status = DISMISSED
        r.dismiss_reason = reason
        r.history.append((on.isoformat(), f"DISMISSED {reason}"))
        self._update(r)


class SignalStore(BaseSignalStore):
    """In-memory store for tests, the replay and the browser demo."""

    def __init__(self) -> None:
        self.records: dict[str, SignalRecord] = {}
        self._processed: set[date] = set()

    def _get(self, key):
        return self.records.get(key)

    def _insert(self, rec):
        if rec.key in self.records:
            return False
        self.records[rec.key] = rec
        return True

    def _update(self, rec):
        self.records[rec.key] = rec

    def _iter(self, account_id=None, crm_account_id=None):
        if account_id is None:
            return list(self.records.values())
        return [r for r in self.records.values()
                if r.account_id == account_id
                or (crm_account_id is not None and r.crm_account_id == crm_account_id)]

    def latest_processed_day(self):
        return max(self._processed) if self._processed else None

    def mark_processed(self, as_of):
        self._processed.add(as_of)

