"""Replay the daily job over history, then measure what it caught and what it missed.

Precision asks: of what we put in front of sellers, how much reached the target outcome?
Recall asks: of the conversions that happened without Sales already involved, how
many did we surface first? The false-negative audit explains each miss.
"""
from __future__ import annotations

import random
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta
from statistics import median
from typing import Optional

from .adapters import InMemoryWarehouse, ListSink
from .config import LOGIC_VERSION, Config
from .features import compute_features
from .models import CrmContext, Decision
from .pipeline import run_daily
from .state import SignalStore
from .synthetic import SyntheticAccount

LOOKBACK_DAYS = 60


class AsOfCrm:
    """CRM snapshot as it looked on a given day (activity logged later is invisible)."""

    def __init__(self, accounts: dict[str, SyntheticAccount]):
        self.accounts = accounts
        self.day: Optional[date] = None

    def context(self, account_id: str) -> CrmContext:
        a = self.accounts.get(account_id)
        if a is None:
            return CrmContext(account_id, [], "none")
        ctx = replace(a.crm, crm_account_ids=list(a.crm.crm_account_ids))
        if ctx.last_activity_date and self.day and ctx.last_activity_date > self.day:
            ctx.last_activity_date, ctx.last_activity_type = None, None
        return ctx


@dataclass
class Replay:
    by_account: dict[str, list[tuple[date, str, list[str]]]]
    delivered: list[tuple[date, str, str]]          # (day, account, key)
    store: SignalStore
    late_days: list[date]


def replay(accounts: list[SyntheticAccount], events, start: date, end: date,
           late_days: tuple[date, ...] = (), config: Config = Config(), seed: int = 11) -> Replay:
    rng = random.Random(seed)
    idx = {a.account_id: a for a in accounts}
    crm = AsOfCrm(idx)
    store, sink = SignalStore(), ListSink()
    wh = InMemoryWarehouse({a.account_id: a.created_on for a in accounts}, events, start)
    by_account: dict[str, list] = defaultdict(list)
    delivered: list = []
    pending_feedback: list[tuple[date, str, str]] = []
    loaded = start
    d = start
    while d <= end:
        if d not in late_days:
            loaded = d
        wh.loaded = loaded
        crm.day = d
        res = run_daily(d, wh, crm, store, sink, config)
        for ev in res.evaluations:
            by_account[ev.account_id].append((d, ev.decision.value, ev.blocking_reasons))
        for key in res.delivered:
            acct = key.split(":")[0]
            delivered.append((d, acct, key))
            pending_feedback.append((d + timedelta(days=rng.randint(1, 4)), acct, key))
        # simulated seller response, a few days after delivery
        still = []
        for when, acct, key in pending_feedback:
            if when > d:
                still.append((when, acct, key))
                continue
            # Outcome-blind on purpose: a simulated seller who only dismisses
            # accounts that won't convert makes every dismissal free and
            # flatters precision. (An earlier version did exactly that.)
            r = rng.random()
            if r < 0.30:
                store.action(key, d)
            elif r < 0.60:
                store.dismiss(key, d, "not_a_fit")
            elif r < 0.75:
                store.snooze(key, d, 14)
            # otherwise the seller never responds
        pending_feedback = still
        d += timedelta(days=1)
    return Replay(dict(by_account), delivered, store, list(late_days))


def _miss_cause(history: list[tuple[date, str, list[str]]], large: bool, moved: bool) -> str:
    decisions = {dec for _, dec, _ in history}
    if Decision.ROUTE.value in decisions:
        return "ownership or identity unresolved"
    if Decision.HOLD.value in decisions:
        return "held (stale data, cooldown, capacity or open episode)"
    if Decision.SUPPRESS.value in decisions:
        return "suppressed (engaged, merged, dismissed or unanswered)"
    if moved:
        return "behavior moved but stayed below every threshold"
    return "large steady account, no change to detect" if large else "no measurable change"


def _moved(features) -> bool:
    """Some movement, short of firing: a near miss rather than a flat account."""
    v = features.consumption_velocity
    return (v is not None and v >= 1.5) or features.users_added_30d >= 2 or features.features_added_30d >= 1


def measure(accounts: list[SyntheticAccount], rp: Replay, start: date, end: date, events) -> dict:
    idx = {a.account_id: a for a in accounts}
    first_surface: dict[str, date] = {}
    for d, acct, _ in rp.delivered:
        first_surface.setdefault(acct, d)

    # precision: per delivered signal, did the account convert within the window after it?
    hits = 0
    for d, acct, _ in rp.delivered:
        e = idx[acct].converted_on
        if e is not None and 0 < (e - d).days <= LOOKBACK_DAYS:
            hits += 1
    precision = hits / len(rp.delivered) if rp.delivered else None
    acct_hit = {acct for d, acct, _ in rp.delivered
                if idx[acct].converted_on is not None and 0 < (idx[acct].converted_on - d).days <= LOOKBACK_DAYS}
    precision_accounts = len(acct_hit) / len(first_surface) if first_surface else None

    # recall denominator: conversions inside the measurable window, Sales not already on it
    events_by: dict[str, list] = defaultdict(list)
    for e in events:
        events_by[e.account_id].append(e)
    volume = defaultdict(float)
    for e in events:
        if e.kind == "consumption":
            volume[e.account_id] += e.amount
    cutoff = sorted(volume.values())[int(len(volume) * 0.9)] if volume else 0
    eligible, caught, lead_times = [], [], []
    misses: list[dict] = []
    engaged_conversions = 0
    for a in accounts:
        e = a.converted_on
        if e is None or not (start + timedelta(days=14) <= e <= end):
            continue
        if a.crm.open_opportunity:
            engaged_conversions += 1
            continue
        eligible.append(a)
        surfaced = [d for d, acct, _ in rp.delivered if acct == a.account_id and 0 < (e - d).days <= LOOKBACK_DAYS]
        if surfaced:
            caught.append(a)
            lead_times.append((e - min(surfaced)).days)
        else:
            hist = [h for h in rp.by_account.get(a.account_id, []) if 0 < (e - h[0]).days <= LOOKBACK_DAYS]
            day_before = min(e - timedelta(days=1), end)
            f = compute_features(a.account_id, events_by.get(a.account_id, []), day_before, a.created_on, day_before)
            misses.append({"account_id": a.account_id, "archetype": a.archetype,
                           "cause": _miss_cause(hist, volume[a.account_id] >= cutoff, _moved(f)),
                           "converted_on": e.isoformat()})
    recall = len(caught) / len(eligible) if eligible else None

    by_arch = {}
    for arch in sorted({a.archetype for a in accounts}):
        el = [a for a in eligible if a.archetype == arch]
        ca = [a for a in caught if a.archetype == arch]
        sig = [x for x in rp.delivered if idx[x[1]].archetype == arch]
        by_arch[arch] = {"accounts": sum(1 for a in accounts if a.archetype == arch),
                         "signals_delivered": len(sig), "eligible_conversions": len(el), "caught": len(ca)}

    statuses = Counter(r.status for r in rp.store.records.values())
    reasons = Counter(r.dismiss_reason for r in rp.store.records.values() if r.dismiss_reason)
    decision_days = Counter(dec for h in rp.by_account.values() for _, dec, _ in h)
    held_on_late = sum(1 for h in rp.by_account.values() for d, dec, br in h
                       if d in rp.late_days and dec == "HOLD" and any("warehouse" in b for b in br))
    held_for_capacity = sum(1 for h in rp.by_account.values() for _, dec, br in h
                            if dec == "HOLD" and br and "at capacity" in br[0])
    dismissal_respected = sum(1 for h in rp.by_account.values() for _, dec, br in h
                              if dec == "SUPPRESS" and any("seller dismissed" in b for b in br))

    coverage, cutoff_30d = coverage_audit(accounts, events, end, set(first_surface))
    steady_missed = {m["account_id"] for m in misses if m["cause"].startswith("large steady")}

    return {
        "logic_version": LOGIC_VERSION,
        "coverage_audit": {
            "listed": len(coverage),
            "consumption_30d_cutoff": round(cutoff_30d, 2),
            "contains_missed_large_steady": len(steady_missed & set(coverage)),
            "missed_large_steady": len(steady_missed),
            "listed_that_converted": sum(1 for a in coverage if idx[a].converted_on),
        },
        "window": [start.isoformat(), end.isoformat()],
        "accounts": len(accounts),
        "signals_delivered": len(rp.delivered),
        "accounts_surfaced": len(first_surface),
        "precision_60d": round(precision, 3) if precision is not None else None,
        "precision_60d_by_account": round(precision_accounts, 3) if precision_accounts is not None else None,
        "recall_60d": round(recall, 3) if recall is not None else None,
        "eligible_conversions": len(eligible),
        "conversions_already_engaged": engaged_conversions,
        "median_lead_time_days": median(lead_times) if lead_times else None,
        "miss_causes": dict(Counter(m["cause"] for m in misses).most_common()),
        "misses": misses,
        "by_archetype": by_arch,
        "signal_status": dict(statuses),
        "dismiss_reasons": dict(reasons),
        "decision_days": dict(decision_days),
        "late_warehouse_days": [d.isoformat() for d in rp.late_days],
        "account_days_held_for_late_data": held_on_late,
        "account_days_suppressed_by_dismissal": dismissal_respected,
        "account_days_held_for_capacity": held_for_capacity,
    }


def coverage_audit(accounts: list[SyntheticAccount], events, as_of: date, surfaced: set[str],
                   top_share: float = 0.1) -> tuple[list[str], float]:
    """The false-negative audit you can run before any outcome exists.

    Signals detect change, so a large account that is already large and stays
    flat never fires. This lists the top accounts by trailing-30-day consumption
    that no signal surfaced and that have no open opportunity or two-way seller
    conversation. It is a coverage review for RevOps, not a seller alert.
    """
    from .config import ONE_WAY_ACTIVITY
    start = as_of - timedelta(days=29)
    vol: dict[str, float] = defaultdict(float)
    for e in events:
        if e.kind == "consumption" and start <= e.day <= as_of:
            vol[e.account_id] += e.amount
    ranked = sorted(vol, key=lambda a: -vol[a])[: max(1, int(len(accounts) * top_share))]
    cutoff = vol[ranked[-1]] if ranked else 0.0
    idx = {a.account_id: a for a in accounts}
    out = []
    for acct in ranked:
        c = idx[acct].crm
        talking = (c.last_activity_date is not None and c.last_activity_type not in ONE_WAY_ACTIVITY
                   and 0 <= (as_of - c.last_activity_date).days <= 30)
        if acct in surfaced or c.open_opportunity or talking:
            continue
        out.append(acct)
    return out, cutoff
