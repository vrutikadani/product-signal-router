"""The daily batch: every account, once per warehouse refresh.

A daily job is enough because the warehouse refreshes daily. Streaming would
make signals arrive faster than the data underneath them changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .adapters import CrmSource, SellerSink, WarehouseSource
from .ai_context import Summarizer
from .config import Config
from .engine import evaluate
from .features import compute_features
from .models import Decision, Evaluation
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
        if ev.decision == Decision.SURFACE:
            rec, created_new = store.record_surfaced(ev)
            if created_new:
                sink.deliver(ev, rec.key)
                result.delivered.append(rec.key)
            else:
                result.duplicates_skipped += 1
    return result
