"""Seeded synthetic world for the evaluation and the demo.

Each account is drawn from an archetype that describes how it behaves, how
the CRM sees it, and how likely it is to reach the target outcome (a
sales-assisted opportunity or upgrade) with a seller's help. The
outcome labels are generated from those archetypes, so the evaluation shows
whether the *measurement* works (precision, recall, false-negative audit),
not whether these signals predict outcomes in any real business.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from .models import CrmContext, ProductEvent

START = date(2026, 3, 1)
END = date(2026, 6, 30)

# name: (share of accounts, base daily consumption, growth multiplier, users added after onset,
#        features added after onset, top-user weight, P(conversion), single spike)
ARCHETYPES = {
    "org_adopter":    (0.10, 90, 3.0, 6, 2, 0.30, 0.55, False),
    "breadth_adopter": (0.08, 70, 1.4, 2, 3, 0.40, 0.30, False),
    "whale":           (0.07, 120, 3.5, 0, 0, 0.95, 0.15, False),
    "spike":           (0.06, 60, 1.0, 0, 0, 0.50, 0.03, True),
    "steady_large":    (0.07, 1800, 1.0, 1, 0, 0.25, 0.30, False),
    "slow_grower":     (0.20, 40, 1.5, 1, 1, 0.40, 0.08, False),
    "flat_small":      (0.42, 25, 1.0, 0, 0, 0.50, 0.02, False),
}
FEATURES = ["core", "integrations", "api", "automations", "approvals", "reporting", "sso"]


@dataclass
class SyntheticAccount:
    account_id: str
    archetype: str
    created_on: date
    onset: date
    converted_on: Optional[date]
    crm: CrmContext


def _crm(rng: random.Random, acct: str, crm_id: str) -> CrmContext:
    r = rng.random()
    if r < 0.06:
        return CrmContext(acct, [], "none")                                    # never matched
    if r < 0.11:
        return CrmContext(acct, [crm_id], "explicit_id", owner_id=None)        # unowned
    if r < 0.14:
        return CrmContext(acct, [crm_id], "explicit_id", owner_id="rep_x", owner_active=False)
    method = "domain" if r < 0.22 else "explicit_id"
    ctx = CrmContext(acct, [crm_id], method, owner_id=f"rep_{rng.randint(1, 12)}")
    if rng.random() < 0.08:
        ctx.open_opportunity = True
    return ctx


def generate(n: int = 400, seed: int = 7) -> tuple[list[SyntheticAccount], list[ProductEvent]]:
    rng = random.Random(seed)
    names = list(ARCHETYPES)
    weights = [ARCHETYPES[a][0] for a in names]
    accounts: list[SyntheticAccount] = []
    events: list[ProductEvent] = []
    for i in range(n):
        acct = f"acct_{i:04d}"
        arch = rng.choices(names, weights)[0]
        _, base, growth, users_after, feats_after, top_w, p_exp, spike = ARCHETYPES[arch]
        created = START + timedelta(days=rng.randint(0, 40))
        onset = created + timedelta(days=rng.randint(35, 80))
        base *= rng.uniform(0.6, 1.5)
        converted_on = None
        if rng.random() < p_exp:
            converted_on = min(onset + timedelta(days=rng.randint(10, 50)), END + timedelta(days=30))
        accounts.append(SyntheticAccount(acct, arch, created, onset, converted_on,
                                         _crm(rng, acct, f"crm_{i:04d}")))

        users = [f"{acct}_u{k}" for k in range(rng.randint(1, 3))]
        for u in users:
            events.append(ProductEvent(acct, created, "user_added", user_id=u))
        first_feature = FEATURES[0]
        events.append(ProductEvent(acct, created, "feature_enabled", feature=first_feature))

        added_users = 0
        added_feats = 0
        d = created
        while d <= END:
            after = d >= onset
            ramp = min(1.0, (d - onset).days / 30) if after else 0.0
            mult = 1 + (growth - 1) * ramp
            if after and added_users < users_after and rng.random() < users_after / 30:
                u = f"{acct}_u{len(users)}"
                users.append(u)
                added_users += 1
                events.append(ProductEvent(acct, d, "user_added", user_id=u))
            if after and added_feats < feats_after and rng.random() < feats_after / 30:
                added_feats += 1
                events.append(ProductEvent(acct, d, "feature_enabled", feature=FEATURES[added_feats]))
            if rng.random() < 0.8:
                amt = base * mult * rng.uniform(0.5, 1.5)
                u = users[0] if rng.random() < top_w else rng.choice(users)
                events.append(ProductEvent(acct, d, "consumption", user_id=u, amount=round(amt, 2)))
            if spike and d == onset:
                events.append(ProductEvent(acct, d, "consumption", user_id=users[0], amount=round(base * 120, 2)))
            d += timedelta(days=1)

        # a few accounts get one-way outreach (a sequence) that nobody answers
        ctx = accounts[-1].crm
        if ctx.owner_id and rng.random() < 0.12:
            ctx.last_activity_date = onset + timedelta(days=rng.randint(0, 20))
            ctx.last_activity_type = "sequence_step"

    # two pairs of product accounts that resolve to one CRM account
    for a, b in ((3, 4), (10, 11)):
        if a < n and b < n:
            accounts[b].crm.crm_account_ids = list(accounts[a].crm.crm_account_ids)
    return accounts, events
