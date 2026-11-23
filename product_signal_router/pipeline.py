"""The daily batch: every account, once per warehouse refresh.

A daily job is enough because the warehouse refreshes daily. Streaming would
make signals arrive faster than the data underneath them changes.

Delivery works like an outbox. The record is written as PENDING_DELIVERY, the
sink is called, and only a returned call marks it DELIVERED. A sink error is
recorded on that account's record and the batch carries on. Replaying the same
day retries an undelivered record once, with the same idempotency key, unless
a newer day has been processed since: the replay is then a backfill and sends
nothing. A record that will never be sent takes no seller capacity on a replay.

Backfill: a day older than the latest day already processed (or older than the
freshness window) is decided and recorded, but nothing is delivered. A seller
should not get a signal about last week because a partition was reloaded.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Optional

from .adapters import CrmSource, PermanentDeliveryError, SellerSink, WarehouseSource
from .ai_context import Summarizer
from .config import Config
from .engine import evaluate
from .features import compute_features
from .models import AccountFeatures, Band, CrmContext, Decision, Evaluation
from .state import DELIVERED, DELIVERY_FAILED, NOT_DELIVERED_BACKFILL, PENDING_DELIVERY, BaseSignalStore


@dataclass
class RunResult:
    as_of: date
    evaluations: list[Evaluation] = field(default_factory=list)
    delivered: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)     # (signal key, error)
    duplicates_skipped: int = 0
    backfill: bool = False

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for ev in self.evaluations:
            out[ev.decision.value] = out.get(ev.decision.value, 0) + 1
        return out


def run_daily(
    as_of: date,
    warehouse: WarehouseSource,
    crm: CrmSource,
    store: BaseSignalStore,
    sink: SellerSink,
    config: Config = Config(),
    summarizer: Optional[Summarizer] = None,
    today: Optional[date] = None,
) -> RunResult:
    """Warehouse rows -> features -> `run_batch`."""
    loaded = warehouse.loaded_through()
    inputs = []
    for acct in warehouse.account_ids():
        created = warehouse.created_on(acct)
        if created > as_of:
            continue
        feats = compute_features(acct, warehouse.events(acct, as_of), as_of, created, data_loaded_through=min(loaded, as_of))
        inputs.append((feats, crm.context(acct)))
    return run_batch(as_of, inputs, store, sink, config, summarizer, today)


def run_batch(
    as_of: date,
    inputs: Iterable[tuple[AccountFeatures, CrmContext]],
    store: BaseSignalStore,
    sink: SellerSink,
    config: Config = Config(),
    summarizer: Optional[Summarizer] = None,
    today: Optional[date] = None,
) -> RunResult:
    """Everything after features: engine, CRM-account merge, seller capacity,
    record and deliver. The browser demo calls this directly with features it
    builds from the form."""
    today = today or as_of
    latest = store.latest_processed_day()
    backfill = (latest is not None and as_of < latest) or (today - as_of).days > config.max_data_lag_days
    result = RunResult(as_of, backfill=backfill)
    for feats, ctx in inputs:
        result.evaluations.append(evaluate(feats, ctx, store, today, config, summarizer))

    _one_signal_per_crm_account(result.evaluations, store)
    _apply_seller_capacity(result.evaluations, store, as_of, config)
    _record_and_deliver(result, store, sink, today)
    store.mark_processed(as_of)
    return result


def _record_and_deliver(result: RunResult, store: BaseSignalStore, sink: SellerSink, today: date) -> None:
    for ev in result.evaluations:
        if ev.decision != Decision.SURFACE:
            continue
        rec, _ = store.record_surfaced(ev, backfill=result.backfill)
        if rec.delivery_status == DELIVERED:
            result.duplicates_skipped += 1       # a replay of a delivered day
            continue
        if result.backfill or rec.delivery_status != PENDING_DELIVERY:
            # A backfill never sends, even a record left pending by an earlier
            # failed run of this day. Otherwise: gave up, or recorded in a backfill.
            continue
        try:
            sink.deliver(ev, rec.key)
        except Exception as exc:   # one account's failure must not stop the batch
            error = f"{type(exc).__name__}: {exc}"
            store.mark_delivery_failed(rec.key, today, error, permanent=isinstance(exc, PermanentDeliveryError))
            result.failed.append((rec.key, error))
            continue
        store.mark_delivered(rec.key, today)
        result.delivered.append(rec.key)


_UNDELIVERABLE = (DELIVERY_FAILED, NOT_DELIVERED_BACKFILL)
_RANK = {Band.HIGH: 3, Band.MEDIUM: 2, Band.LOW: 1, Band.NONE: 0}


def _one_signal_per_crm_account(evals: list[Evaluation], store: BaseSignalStore) -> None:
    """Several product accounts can resolve to one CRM account (a parent company
    with two workspaces, a reseller, a re-signup). The seller works the CRM
    account, so only one of them may surface per day. The winner is chosen
    deterministically so a replay picks the same one."""
    groups: dict[str, list[Evaluation]] = {}
    for ev in evals:
        if ev.decision == Decision.SURFACE and ev.crm_account_id:
            groups.setdefault(ev.crm_account_id, []).append(ev)
    for crm_id, group in groups.items():
        if len(group) < 2:
            group[0].rules_passed.append(f"one signal per CRM account ({crm_id})")
            continue
        already = [ev for ev in group if store.get(store.key_for(ev))]
        winner = already[0] if already else sorted(
            group, key=lambda e: (-_RANK[e.strength], -_RANK[e.confidence], e.account_id))[0]
        for ev in group:
            if ev is winner:
                ev.rules_passed.append(f"one signal per CRM account ({crm_id}; chosen over "
                                       f"{len(group) - 1} other product account(s))")
                ev.seller_notes.append(
                    "also signalling on the same CRM account: "
                    + ", ".join(e.account_id for e in group if e is not winner))
                continue
            ev.decision = Decision.SUPPRESS
            ev.actionable = False
            ev.ai_context = {"used": False, "reason": "merged into another signal; AI only runs on surfaced signals"}
            ev.blocking_reasons.insert(0, f"merged into {winner.account_id} (same CRM account {crm_id})")


def _apply_seller_capacity(evals: list[Evaluation], store: BaseSignalStore, as_of: date, config: Config) -> None:
    """Each owner gets at most N new signals per rolling week. Today's candidates
    are ranked (strength, confidence, number of families, account id) and the
    overflow is held, visibly, instead of being delivered and ignored. Signals
    already recorded for today (a replay) keep their slot, unless they can never
    be sent (delivery gave up, or recorded during a backfill): those take no
    slot, so the next candidate is not held behind them."""
    by_owner: dict[str, list[Evaluation]] = {}
    for ev in evals:
        if ev.decision == Decision.SURFACE and ev.owner_id:
            by_owner.setdefault(ev.owner_id, []).append(ev)
    week_start = as_of - timedelta(days=6)
    for owner, group in by_owner.items():
        used = store.delivered_to_owner(owner, week_start, as_of)
        recs = {id(ev): store.get(store.key_for(ev)) for ev in group}
        dead = [ev for ev in group if recs[id(ev)] and recs[id(ev)].delivery_status in _UNDELIVERABLE]
        for ev in dead:
            ev.rules_passed.append(f"owner {owner} capacity not used "
                                   f"({recs[id(ev)].delivery_status}: will not be sent)")
        replayed = [ev for ev in group if recs[id(ev)] and ev not in dead]
        fresh = sorted((ev for ev in group if not recs[id(ev)]),
                       key=lambda e: (-_RANK[e.strength], -_RANK[e.confidence],
                                      -sum(f.fired for f in e.families), e.account_id))
        cap = config.seller_capacity_per_week
        room = max(0, cap - used - len(replayed))
        for i, ev in enumerate(replayed + fresh[:room]):
            ev.rules_passed.append(f"owner {owner} within weekly capacity ({used + i + 1} of {cap} in 7 days)")
        for ev in fresh[room:]:
            ev.decision = Decision.HOLD
            ev.actionable = False
            ev.blocking_reasons.insert(0, f"owner {owner} is at capacity "
                                          f"({config.seller_capacity_per_week} new signals per 7 days)")
            ev.ai_context = {"used": False, "reason": "held for capacity; AI only runs on surfaced signals"}
