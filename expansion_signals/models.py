"""Data shapes shared by every stage of the pipeline.

Everything here is plain dataclasses so the engine runs on the standard
library alone (the in-browser demo loads these same files in Pyodide).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from enum import Enum
from typing import Optional


class Band(str, Enum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Decision(str, Enum):
    SURFACE = "SURFACE"            # put in front of the owning seller
    MONITOR = "MONITOR"            # real but weak; keep watching, no seller time
    HOLD = "HOLD"                  # strong, but not actionable right now (cooldown, snooze, stale data)
    SUPPRESS = "SUPPRESS"          # strong, but someone is already on it or the seller said no
    ROUTE = "ROUTE"                # strong, but nobody can act on it until ownership/identity is fixed
    NO_SIGNAL = "NO_SIGNAL"


@dataclass
class ProductEvent:
    """One row from the product warehouse. Amounts are in the product's unit of value."""
    account_id: str
    day: date
    kind: str                     # consumption | user_added | feature_enabled | login
    user_id: Optional[str] = None
    amount: float = 0.0
    feature: Optional[str] = None
    event_id: Optional[str] = None


@dataclass
class AccountFeatures:
    """Daily, per-account aggregates. This is the only product data the decision sees."""
    account_id: str
    as_of: date                   # the day these features describe
    data_loaded_through: date     # latest warehouse partition actually present
    account_age_days: int
    consumption_30d: float
    consumption_prior_30d: float
    largest_event_share_30d: float    # biggest single consumption event / consumption_30d
    top_user_share_30d: float         # biggest single user's consumption / consumption_30d
    users_total: int
    users_added_30d: int
    active_users_30d: int
    features_total: int
    features_added_30d: int

    @property
    def consumption_velocity(self) -> Optional[float]:
        if self.consumption_prior_30d <= 0:
            return None
        return self.consumption_30d / self.consumption_prior_30d


@dataclass
class CrmContext:
    """What the CRM knows. The engine never writes ownership; it only reads it."""
    account_id: str
    crm_account_ids: list[str] = field(default_factory=list)  # identity resolution result
    match_method: str = "explicit_id"     # explicit_id | domain | none
    owner_id: Optional[str] = None
    owner_active: bool = True
    open_opportunity: bool = False
    last_activity_date: Optional[date] = None
    last_activity_type: Optional[str] = None   # see config.TWO_WAY_ACTIVITY / ONE_WAY_ACTIVITY

    @property
    def crm_account_id(self) -> Optional[str]:
        return self.crm_account_ids[0] if len(self.crm_account_ids) == 1 else None


@dataclass
class SignalFamilyResult:
    family: str                   # value | adoption | breadth
    fired: bool
    band: Band
    evidence: list[str]


@dataclass
class Evaluation:
    account_id: str
    as_of: date
    logic_version: str
    families: list[SignalFamilyResult]
    strength: Band
    confidence: Band
    confidence_reasons: list[str]
    actionable: bool
    blocking_reasons: list[str]
    rules_passed: list[str]
    decision: Decision
    crm_account_id: Optional[str] = None
    owner_id: Optional[str] = None
    seller_notes: list[str] = field(default_factory=list)
    ai_context: Optional[dict] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["as_of"] = self.as_of.isoformat()
        d["strength"] = self.strength.value
        d["confidence"] = self.confidence.value
        d["decision"] = self.decision.value
        for f in d["families"]:
            f["band"] = f["band"].value if isinstance(f["band"], Band) else f["band"]
        return d
