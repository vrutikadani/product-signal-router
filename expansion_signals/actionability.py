"""Actionability: can a specific seller usefully act on this today?

A strong, trustworthy signal can still be non-actionable. Each rule returns
(passed, label, decision_if_blocked). The first blocking rule decides the outcome;
the rest are still reported so the inspector shows the whole picture.

Engine memory (cooldown, snooze, dismissal) only looks at signals from *earlier*
days. A signal recorded for today is the result of this evaluation, not a
reason to block it; otherwise replaying a day changes the answer.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from .config import ONE_WAY_ACTIVITY, Config
from .models import AccountFeatures, Band, CrmContext, Decision
from .state import DISMISSED, EXPIRED, OPEN, SNOOZED, SignalStore

Rule = tuple[bool, str, Optional[Decision]]
_ORDER = [Band.NONE, Band.LOW, Band.MEDIUM, Band.HIGH]


def check_rules(f: AccountFeatures, crm: CrmContext, store: SignalStore, today: date, c: Config,
                strength: Band = Band.HIGH) -> tuple[list[Rule], list[str]]:
    rules: list[Rule] = []
    notes: list[str] = []

    # identity: exactly one CRM account
    n = len(crm.crm_account_ids)
    rules.append((n == 1, f"identity resolves to one CRM account (found {n})", Decision.ROUTE))

    # ownership: an owner who exists and still works here
    rules.append((crm.owner_id is not None, "account has an owner", Decision.ROUTE))
    if crm.owner_id is not None:
        rules.append((crm.owner_active, "owner is an active user", Decision.ROUTE))

    # already being worked: an open opportunity or a two-way conversation
    rules.append((not crm.open_opportunity, "no open opportunity", Decision.SUPPRESS))
    recent = (crm.last_activity_date is not None
              and (today - crm.last_activity_date).days <= c.recent_activity_days)
    one_way = crm.last_activity_type in ONE_WAY_ACTIVITY
    if recent and one_way:
        days = (today - crm.last_activity_date).days
        notes.append(f"{crm.last_activity_type.replace('_', ' ')} sent {days} days ago with no reply logged")
    rules.append((not (recent and not one_way),
                  f"no two-way seller conversation in last {c.recent_activity_days} days", Decision.SUPPRESS))

    # data freshness
    lag = (today - f.data_loaded_through).days
    rules.append((lag <= c.max_data_lag_days, f"warehouse data is current (lag {lag}d)", Decision.HOLD))

    # engine memory: earlier signals for this account (or its CRM account) only
    last = store.last_surfaced(f.account_id, before=f.as_of, crm_account_id=crm.crm_account_id)
    if last is not None:
        age = (today - last.as_of).days
        if last.status == DISMISSED:
            window = {"not_a_fit": c.dismiss_not_a_fit_days,
                      "bad_timing": c.dismiss_bad_timing_days,
                      "already_engaged": c.dismiss_already_engaged_days}.get(last.dismiss_reason)
            if last.dismiss_reason == "wrong_owner":
                reassigned = crm.owner_id is not None and crm.owner_id != last.owner_id
                rules.append((reassigned, "seller reported wrong owner; waiting for reassignment", Decision.ROUTE))
            elif last.dismiss_reason == "bad_data":
                rules.append((False, "seller reported bad data; held for review", Decision.HOLD))
            elif window is not None:
                rose = _ORDER.index(strength) > _ORDER.index(Band(last.strength))
                active = age < window and not rose
                label = (f"seller dismissed {age}d ago ({last.dismiss_reason}); "
                         f"respected for {window}d unless strength rises above {last.strength}")
                rules.append((not active, label, Decision.SUPPRESS))
        elif last.status == OPEN and age < c.open_signal_expiry_days:
            # A signal is an episode, not a daily alert: if the seller has not
            # answered the last one, do not send another on top of it.
            rules.append((False, f"open signal from {age}d ago still waiting on the seller", Decision.HOLD))
        elif last.status in (OPEN, EXPIRED):
            # Expired without an answer. Until 1.5 this fell through to the
            # cooldown rule, so the same signal was re-sent every 21 days.
            rose = _ORDER.index(strength) > _ORDER.index(Band(last.strength))
            quiet = age < c.unanswered_quiet_days and not rose
            rules.append((not quiet, f"signal from {age}d ago expired unanswered; quiet for "
                          f"{c.unanswered_quiet_days}d unless strength rises above {last.strength}",
                          Decision.SUPPRESS))
        else:
            rules.append((age >= c.cooldown_days, f"not surfaced in last {c.cooldown_days} days", Decision.HOLD))
            snoozed = last.status == SNOOZED and last.snoozed_until is not None and last.snoozed_until > today
            rules.append((not snoozed, "not snoozed by the seller", Decision.HOLD))
    return rules, notes
