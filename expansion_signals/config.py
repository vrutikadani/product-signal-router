"""Every threshold the engine uses, in one place, with its reason.

None of these numbers are fitted. They are starting points that a backtest
should replace (see docs/EVALUATION.md).
"""
from __future__ import annotations

from dataclasses import dataclass

LOGIC_VERSION = "1.5.0"


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
    max_single_event_share: float = 0.5  # above this, velocity is recomputed without that event
    max_data_lag_days: int = 0           # features for day D need D's partition; any gap = stale

    # actionability
    recent_activity_days: int = 14
    cooldown_days: int = 14
    open_signal_expiry_days: int = 21    # an unanswered signal stays the one open signal until it expires
    unanswered_quiet_days: int = 90      # after expiry, silence counts as a soft "no" until this age, unless strength rises
    seller_capacity_per_week: int = 5    # new signals per owner per rolling 7 days; set from a capacity review
    dismiss_not_a_fit_days: int = 90     # unless strength rises above the band it was dismissed at
    dismiss_bad_timing_days: int = 30
    dismiss_already_engaged_days: int = 30


# CRM activity that means a conversation is happening. One-way touches
# (sequence steps, sent emails) do not suppress a signal; unknown types do,
# because guessing "not engaged" puts a second seller on a live conversation.
TWO_WAY_ACTIVITY = {"meeting", "call_connected", "reply", "demo"}
ONE_WAY_ACTIVITY = {"sequence_step", "email_sent", "call_attempt"}
