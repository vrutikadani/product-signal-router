"""Product events -> daily account features.

In production this is a SQL model in the warehouse. It lives in Python here so
the tests and the demo can run it. The logic is the same either way: two
trailing 30-day windows, plus a few shape measures that catch misleading totals.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Iterable

from .models import AccountFeatures, ProductEvent


ONBOARDING_DAYS = 7   # users and features set up in the first week are signup, not expansion


def compute_features(
    account_id: str,
    events: Iterable[ProductEvent],
    as_of: date,
    created_on: date,
    data_loaded_through: date | None = None,
) -> AccountFeatures:
    seen: set[str] = set()
    unique = []
    for e in events:
        if e.account_id != account_id or e.day > as_of:
            continue
        if e.event_id is not None:
            # Warehouse loads can replay a partition. Without this, a duplicated
            # month doubles consumption and fires the value family on its own.
            if e.event_id in seen:
                continue
            seen.add(e.event_id)
        unique.append(e)
    events = unique
    start_30 = as_of - timedelta(days=29)
    start_60 = as_of - timedelta(days=59)

    cons_30 = [e for e in events if e.kind == "consumption" and e.day >= start_30]
    cons_prior = [e for e in events if e.kind == "consumption" and start_60 <= e.day < start_30]
    total_30 = sum(e.amount for e in cons_30)
    total_prior = sum(e.amount for e in cons_prior)

    largest = max((e.amount for e in cons_30), default=0.0)
    by_user: dict[str, float] = defaultdict(float)
    for e in cons_30:
        by_user[e.user_id or "_unknown"] += e.amount
    top_user = max(by_user.values(), default=0.0)

    users = {e.user_id for e in events if e.kind == "user_added"}
    after_onboarding = created_on + timedelta(days=ONBOARDING_DAYS)
    users_30 = {e.user_id for e in events
                if e.kind == "user_added" and e.day >= start_30 and e.day >= after_onboarding}
    active = {e.user_id for e in events if e.kind in ("login", "consumption") and e.day >= start_30 and e.user_id}
    feats = {e.feature for e in events if e.kind == "feature_enabled"}
    feats_30 = {e.feature for e in events
                if e.kind == "feature_enabled" and e.day >= start_30 and e.day >= after_onboarding}

    loaded = data_loaded_through or max((e.day for e in events), default=as_of)

    return AccountFeatures(
        account_id=account_id,
        as_of=as_of,
        data_loaded_through=loaded,
        account_age_days=(as_of - created_on).days,
        consumption_30d=round(total_30, 2),
        consumption_prior_30d=round(total_prior, 2),
        largest_event_share_30d=round(largest / total_30, 3) if total_30 else 0.0,
        top_user_share_30d=round(top_user / total_30, 3) if total_30 else 0.0,
        users_total=len(users),
        users_added_30d=len(users_30),
        active_users_30d=len(active),
        features_total=len(feats),
        features_added_30d=len(feats_30),
    )
