"""The daily batch: every account, once per warehouse refresh.

A daily job is enough because the warehouse refreshes daily. Streaming would
make signals arrive faster than the data underneath them changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from .adapters import CrmSource, SellerSink, WarehouseSource
from .ai_context import Summarizer
from .config import Config
from .engine import evaluate
from .features import compute_features
from .models import Band, Decision, Evaluation
from .state import SignalStore


@dataclass
class RunResult:
    as_of: date
    evaluations: list[Evaluation] = field(default_factory=list)
    delivered: list[str] = field(default_factory=list)
    duplicates_skipped: int = 0

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for ev in self.evaluations:
            out[ev.decision.value] = out.get(ev.decision.value, 0) + 1
        return out


def run_daily(
    as_of: date,
    warehouse: WarehouseSource,
    crm: CrmSource,
    store: SignalStore,
    sink: SellerSink,
    config: Config = Config(),
    summarizer: Optional[Summarizer] = None,
    today: Optional[date] = None,
) -> RunResult:
    today = today or as_of
    loaded = warehouse.loaded_through()
    result = RunResult(as_of)
    for acct in warehouse.account_ids():
        created = warehouse.created_on(acct)
        if created > as_of:
            continue
        feats = compute_features(acct, warehouse.events(acct, as_of), as_of, created, data_loaded_through=min(loaded, as_of))
        ev = evaluate(feats, crm.context(acct), store, today, config, summarizer)
        result.evaluations.append(ev)

    _one_signal_per_crm_account(result.evaluations, store)
    _apply_seller_capacity(result.evaluations, store, as_of, config)

    for ev in result.evaluations:
        if ev.decision == Decision.SURFACE:
            rec, created_new = store.record_surfaced(ev)
            if created_new:
                sink.deliver(ev, rec.key)
                result.delivered.append(rec.key)
            else:
                result.duplicates_skipped += 1
    return result


_RANK = {Band.HIGH: 3, Band.MEDIUM: 2, Band.LOW: 1, Band.NONE: 0}


def _one_signal_per_crm_account(evals: list[Evaluation], store: SignalStore) -> None:
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
            continue
        already = [ev for ev in group if store.get(store.key_for(ev))]
        winner = already[0] if already else sorted(
            group, key=lambda e: (-_RANK[e.strength], -_RANK[e.confidence], e.account_id))[0]
        for ev in group:
            if ev is winner:
                ev.seller_notes.append(
                    "also signalling on the same CRM account: "
                    + ", ".join(e.account_id for e in group if e is not winner))
                continue
            ev.decision = Decision.SUPPRESS
            ev.actionable = False
            ev.ai_context = {"used": False, "reason": "merged into another signal; AI only runs on surfaced signals"}
            ev.blocking_reasons.insert(0, f"merged into {winner.account_id} (same CRM account {crm_id})")


def _apply_seller_capacity(evals: list[Evaluation], store: SignalStore, as_of: date, config: Config) -> None:
    """Each owner gets at most N new signals per rolling week. Today's candidates
    are ranked (strength, confidence, number of families, account id) and the
    overflow is held, visibly, instead of being delivered and ignored. Signals
    already recorded for today (a replay) keep their slot."""
    by_owner: dict[str, list[Evaluation]] = {}
    for ev in evals:
        if ev.decision == Decision.SURFACE and ev.owner_id:
            by_owner.setdefault(ev.owner_id, []).append(ev)
    week_start = as_of - timedelta(days=6)
    for owner, group in by_owner.items():
        used = store.delivered_to_owner(owner, week_start, as_of)
        replayed = [ev for ev in group if store.get(store.key_for(ev))]
        fresh = sorted((ev for ev in group if ev not in replayed),
                       key=lambda e: (-_RANK[e.strength], -_RANK[e.confidence],
                                      -sum(f.fired for f in e.families), e.account_id))
        room = max(0, config.seller_capacity_per_week - used - len(replayed))
        for ev in fresh[room:]:
            ev.decision = Decision.HOLD
            ev.actionable = False
            ev.blocking_reasons.insert(0, f"owner {owner} is at capacity "
                                          f"({config.seller_capacity_per_week} new signals per 7 days)")
            ev.ai_context = {"used": False, "reason": "held for capacity; AI only runs on surfaced signals"}
