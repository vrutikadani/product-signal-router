from __future__ import annotations

from datetime import date, timedelta

from expansion_signals.models import AccountFeatures, CrmContext, ProductEvent

TODAY = date(2026, 6, 30)


def feats(**kw) -> AccountFeatures:
    base = dict(
        account_id="acct_1", as_of=TODAY, data_loaded_through=TODAY, account_age_days=90,
        consumption_30d=9000.0, consumption_prior_30d=4000.0, largest_event_share_30d=0.05,
        top_user_share_30d=0.3, users_total=6, users_added_30d=4, active_users_30d=6,
        features_total=3, features_added_30d=1,
    )
    base.update(kw)
    return AccountFeatures(**base)


def crm(**kw) -> CrmContext:
    base = dict(account_id="acct_1", crm_account_ids=["001A"], match_method="explicit_id",
                owner_id="rep_7", owner_active=True, open_opportunity=False, last_activity_date=None)
    base.update(kw)
    return CrmContext(**base)


def daily_events(account_id, start: date, days: int, per_day: float, users: list[str], kind="consumption"):
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        u = users[i % len(users)]
        out.append(ProductEvent(account_id, d, kind, user_id=u, amount=per_day, event_id=f"{account_id}-{d}-{i}"))
    return out
