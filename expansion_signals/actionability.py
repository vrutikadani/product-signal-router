"""Actionability: can a specific seller usefully act on this today?

A strong, trustworthy signal can still be non-actionable. Each rule returns
(passed, label, decision_if_blocked). The first blocking rule decides the outcome;
the rest are still reported so the inspector shows the whole picture.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from .config import Config
from .models import AccountFeatures, CrmContext, Decision
from .state import SNOOZED, SignalStore

Rule = tuple[bool, str, Optional[Decision]]


def check_rules(f: AccountFeatures, crm: CrmContext, store: SignalStore, today: date, c: Config) -> list[Rule]:
    rules: list[Rule] = []

    # identity: exactly one CRM account
    n = len(crm.crm_account_ids)
    rules.append((n == 1, f"identity resolves to one CRM account (found {n})", Decision.ROUTE))

    # ownership
    rules.append((crm.owner_id is not None, "account has an owner", Decision.ROUTE))

    # already being worked
    rules.append((not crm.open_opportunity, "no open opportunity", Decision.SUPPRESS))
    recent = (crm.last_activity_date is not None
              and (today - crm.last_activity_date).days <= c.recent_activity_days)
    rules.append((not recent, f"no seller activity in last {c.recent_activity_days} days", Decision.SUPPRESS))

    # data freshness
    lag = (today - f.data_loaded_through).days
    rules.append((lag <= c.max_data_lag_days, f"warehouse data is current (lag {lag}d)", Decision.HOLD))

    # engine memory
    last = store.last_surfaced(f.account_id)
    in_cooldown = last is not None and (today - last.as_of).days < c.cooldown_days
    rules.append((not in_cooldown, f"not surfaced in last {c.cooldown_days} days", Decision.HOLD))
    snoozed = last is not None and last.status == SNOOZED and last.snoozed_until and last.snoozed_until > today
    rules.append((not snoozed, "not snoozed by the seller", Decision.HOLD))
    return rules
