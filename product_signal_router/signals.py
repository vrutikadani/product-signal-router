"""Signal strength and confidence.

Strength answers "how much meaningful change in product behavior is there?"
Confidence answers "how far should we trust that reading?"
Neither says whether a seller should act. That is actionability.py.
"""
from __future__ import annotations

from datetime import date

from .config import Config
from .models import AccountFeatures, Band, SignalFamilyResult

_ORDER = [Band.NONE, Band.LOW, Band.MEDIUM, Band.HIGH]


def _min(a: Band, b: Band) -> Band:
    return _ORDER[min(_ORDER.index(a), _ORDER.index(b))]


def value_family(f: AccountFeatures, c: Config) -> SignalFamilyResult:
    v = f.consumption_velocity
    ev = [f"consumption 30d {f.consumption_30d:,.0f} vs prior {f.consumption_prior_30d:,.0f}"]
    if v is not None:
        ev.append(f"velocity {v:.1f}x")
    if f.account_age_days < 60:
        # The prior window starts before the account existed, so "growth" is the
        # onboarding ramp. v1 only lowered confidence here, and MEDIUM confidence
        # still surfaces, so every new large account looked like fast growth.
        ev.append(f"account is {f.account_age_days} days old; velocity needs two full 30-day windows")
        return SignalFamilyResult("value", False, Band.NONE, ev)
    if v is not None and f.largest_event_share_30d > c.max_single_event_share:
        # Recompute without the single largest event. If the growth disappears,
        # it was one transaction, not a trend.
        largest = f.largest_event_share_30d * f.consumption_30d
        v_ex = (f.consumption_30d - largest) / f.consumption_prior_30d
        ev.append(f"one event is {f.largest_event_share_30d:.0%} of 30d consumption; "
                  f"velocity without it {v_ex:.1f}x")
        v = v_ex
    if f.consumption_30d < c.value_floor_30d or v is None or v < c.value_velocity_min:
        return SignalFamilyResult("value", False, Band.NONE, ev)
    band = Band.HIGH if v >= c.value_velocity_high else Band.MEDIUM
    return SignalFamilyResult("value", True, band, ev)


def adoption_family(f: AccountFeatures, c: Config) -> SignalFamilyResult:
    n = f.users_added_30d
    ev = [f"{n} user{'s' if n != 1 else ''} added in 30d after signup week "
          f"({f.users_total} total, {f.active_users_30d} active)"]
    if f.users_added_30d < c.adoption_users_added_min:
        return SignalFamilyResult("adoption", False, Band.NONE, ev)
    band = Band.HIGH if f.users_added_30d >= c.adoption_users_added_high else Band.MEDIUM
    return SignalFamilyResult("adoption", True, band, ev)


def breadth_family(f: AccountFeatures, c: Config) -> SignalFamilyResult:
    n = f.features_added_30d
    ev = [f"{n} feature{'s' if n != 1 else ''} enabled in 30d after signup week ({f.features_total} in use)"]
    if f.features_added_30d < c.breadth_features_added_min:
        return SignalFamilyResult("breadth", False, Band.NONE, ev)
    band = Band.HIGH if f.features_total >= c.breadth_features_total_high else Band.MEDIUM
    return SignalFamilyResult("breadth", True, band, ev)


def strength_reason(fams: list[SignalFamilyResult]) -> str:
    n = sum(x.fired for x in fams)
    if n == 0:
        return "no family fired"
    if n == 1:
        return "1 of 3 families fired; a single family is MEDIUM at most"
    return f"{n} of 3 families fired; two or more independent families is HIGH"


def evaluate_strength(f: AccountFeatures, c: Config) -> tuple[list[SignalFamilyResult], Band]:
    fams = [value_family(f, c), adoption_family(f, c), breadth_family(f, c)]
    fired = [x for x in fams if x.fired]
    if not fired:
        return fams, Band.NONE
    if len(fired) >= 2:
        return fams, Band.HIGH
    # One family alone is MEDIUM. Until 1.2 a single MEDIUM family was LOW (monitor
    # only). The replay showed that rule cut recall from 0.42 to 0.16 without
    # improving precision, so volume is now controlled by seller capacity
    # (pipeline.py), not by understating strength.
    return fams, Band.MEDIUM


def evaluate_confidence(f: AccountFeatures, crm_match_method: str, today: date, c: Config) -> tuple[Band, list[str]]:
    band = Band.HIGH
    reasons: list[str] = []
    lag = (today - f.data_loaded_through).days
    if lag > c.max_data_lag_days:
        band = _min(band, Band.LOW)
        reasons.append(f"warehouse data is {lag} days behind")
    if f.account_age_days < c.min_history_days:
        band = _min(band, Band.MEDIUM)
        reasons.append(f"only {f.account_age_days} days of history")
    if f.top_user_share_30d > c.max_top_user_share:
        band = _min(band, Band.MEDIUM)
        reasons.append(f"{f.top_user_share_30d:.0%} of consumption from one user")
    if crm_match_method == "domain":
        band = _min(band, Band.MEDIUM)
        reasons.append("CRM account matched by domain, not by ID")
    if not reasons:
        reasons.append("fresh data, enough history, usage spread across users, ID-matched")
    return band, reasons
