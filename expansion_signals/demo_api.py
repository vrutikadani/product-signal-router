"""Thin JSON layer the browser demo calls (through Pyodide). The decision logic
is the same engine the tests and the evaluation use; nothing is re-implemented
in JavaScript."""
from __future__ import annotations

import json
from datetime import date, timedelta

from .ai_context import TemplateSummarizer
from .config import LOGIC_VERSION, Config
from .engine import evaluate
from .models import AccountFeatures, CrmContext
from .state import SignalStore

BASE_DAY = date(2026, 6, 30)
_stores: dict[str, SignalStore] = {}
_clock: dict[str, int] = {}
_last_key: dict[str, str] = {}

OWNERSHIP = {
    "resolved":       dict(crm_account_ids=["crm_1"], match_method="explicit_id", owner_id="rep_1", owner_active=True),
    "domain_match":   dict(crm_account_ids=["crm_1"], match_method="domain", owner_id="rep_1", owner_active=True),
    "owner_inactive": dict(crm_account_ids=["crm_1"], match_method="explicit_id", owner_id="rep_9", owner_active=False),
    "unowned":        dict(crm_account_ids=["crm_1"], match_method="explicit_id", owner_id=None),
    "two_matches":    dict(crm_account_ids=["crm_1", "crm_2"], match_method="domain", owner_id="rep_1"),
    "no_match":       dict(crm_account_ids=[], match_method="none", owner_id=None),
}


def _inputs(p: dict, today: date) -> tuple[AccountFeatures, CrmContext]:
    c30 = float(p["consumption_30d"])
    vel = float(p["velocity"])
    prior = c30 / vel if vel > 0 else 0.0
    f = AccountFeatures(
        account_id=p["account"], as_of=today,
        data_loaded_through=today - timedelta(days=int(p.get("data_lag_days", 0))),
        account_age_days=int(p["account_age_days"]) + (today - BASE_DAY).days,
        consumption_30d=c30, consumption_prior_30d=round(prior, 2),
        largest_event_share_30d=float(p.get("largest_event_share", 0.05)),
        top_user_share_30d=float(p["top_user_share"]),
        users_total=int(p["users_total"]), users_added_30d=int(p["users_added_30d"]),
        active_users_30d=int(p["users_total"]),
        features_total=int(p["features_total"]), features_added_30d=int(p["features_added_30d"]),
    )
    own = OWNERSHIP[p["ownership"]]
    act = p.get("activity", "none")
    act_days = int(p.get("activity_days_ago", 5))
    crm = CrmContext(
        account_id=p["account"], open_opportunity=bool(p["open_opportunity"]),
        last_activity_date=None if act == "none" else BASE_DAY - timedelta(days=act_days),
        last_activity_type=None if act == "none" else act,
        **own,
    )
    return f, crm


def run(payload_json: str) -> str:
    p = json.loads(payload_json)
    acct = p["account"]
    store = _stores.setdefault(acct, SignalStore())
    today = BASE_DAY + timedelta(days=_clock.get(acct, 0))
    f, crm = _inputs(p, today)
    summ = TemplateSummarizer() if p.get("ai", True) else None
    ev = evaluate(f, crm, store, today, Config(), summ)
    signal = None
    if ev.decision.value == "SURFACE" and p.get("record", True):
        rec, _ = store.record_surfaced(ev)
        _last_key[acct] = rec.key
    key = _last_key.get(acct)
    if key and store.get(key):
        r = store.get(key)
        signal = {"key": r.key, "status": r.status, "history": r.history,
                  "dismiss_reason": r.dismiss_reason, "strength_at_signal": r.strength}
    out = ev.to_dict()
    out.update({
        "today": today.isoformat(),
        "days_advanced": _clock.get(acct, 0),
        "logic_version": LOGIC_VERSION,
        "velocity": f.consumption_velocity,
        "crm": {"identity": crm.crm_account_ids, "match_method": crm.match_method, "owner": crm.owner_id,
                "owner_active": crm.owner_active, "open_opportunity": crm.open_opportunity,
                "last_activity": None if not crm.last_activity_date else
                f"{crm.last_activity_type} on {crm.last_activity_date.isoformat()}"},
        "coverage_flag": (ev.decision.value == "NO_SIGNAL" and f.consumption_30d >= float(p.get("coverage_cutoff", 1e18))
                          and not crm.open_opportunity),
        "signal": signal,
    })
    return json.dumps(out)


def feedback(account: str, action: str, reason: str = "") -> str:
    store = _stores.get(account)
    key = _last_key.get(account)
    if not store or not key:
        return json.dumps({"ok": False, "error": "no surfaced signal to respond to"})
    today = BASE_DAY + timedelta(days=_clock.get(account, 0))
    if action == "action":
        store.action(key, today)
    elif action == "snooze":
        store.snooze(key, today, 14)
    elif action == "dismiss":
        store.dismiss(key, today, reason)
    return json.dumps({"ok": True})


def advance(account: str, days: int) -> None:
    _clock[account] = _clock.get(account, 0) + int(days)


def reset(account: str | None = None) -> None:
    for d in (_stores, _clock, _last_key):
        if account is None:
            d.clear()
        else:
            d.pop(account, None)
