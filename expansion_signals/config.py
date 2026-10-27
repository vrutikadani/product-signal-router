"""Every threshold the engine uses, in one place, with its reason.

None of these numbers are fitted. They are starting points that a backtest
should replace (see docs/EVALUATION.md).
"""
from __future__ import annotations

from dataclasses import dataclass

LOGIC_VERSION = "1.0.0"


@dataclass(frozen=True)
class Config:
    # value / consumption expansion
    value_floor_30d: float = 4000.0      # below this, growth is noise for a seller
    value_velocity_min: float = 2.0      # trailing 30d vs prior 30d
    value_velocity_high: float = 3.0

    # organizational adoption
    adoption_users_added_min: int = 3
    adoption_users_added_high: int = 6

    # product breadth / operational embedding
    breadth_features_added_min: int = 2
    breadth_features_total_high: int = 4

    # confidence
    min_history_days: int = 45           # velocity on less history is mostly onboarding ramp
    max_top_user_share: float = 0.8      # one person doing everything is not an organization
    max_data_lag_days: int = 1           # warehouse refreshes daily; >1 day behind = stale

    # actionability
    recent_activity_days: int = 14
    cooldown_days: int = 14
