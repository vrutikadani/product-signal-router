from datetime import date, timedelta

from expansion_signals import Band, Decision, SignalStore, evaluate
from expansion_signals.adapters import InMemoryCrm, InMemoryWarehouse, ListSink
from expansion_signals.config import Config
from expansion_signals.features import compute_features
from expansion_signals.models import ProductEvent
from expansion_signals.pipeline import run_daily

from helpers import TODAY, crm, daily_events, feats


def test_new_account_ramp_is_not_value_expansion():
    # 35-day-old account: the prior window is mostly before it existed
    f = feats(account_age_days=35, consumption_30d=50000, consumption_prior_30d=2000,
              users_added_30d=0, features_added_30d=0)
    ev = evaluate(f, crm(), SignalStore(), TODAY)
    assert ev.decision == Decision.NO_SIGNAL


def test_new_account_can_still_show_early_adoption():
    f = feats(account_age_days=35, consumption_30d=3000, consumption_prior_30d=500,
              users_added_30d=7, features_added_30d=0)
    ev = evaluate(f, crm(), SignalStore(), TODAY)
    assert ev.strength == Band.MEDIUM and ev.decision == Decision.SURFACE
    assert ev.confidence == Band.MEDIUM   # short history still lowers trust


def test_signup_week_users_are_not_adoption():
    created = TODAY - timedelta(days=20)
    rows = [ProductEvent("a", created, "user_added", user_id=f"u{i}") for i in range(5)]
    f = compute_features("a", rows, TODAY, created)
    assert f.users_total == 5 and f.users_added_30d == 0


def test_capacity_holds_overflow_and_ranks():
    start = TODAY - timedelta(days=120)
    created, rows, ctx = {}, [], {}
    for i in range(7):
        a = f"a{i}"
        created[a] = start
        rows += daily_events(a, start, 90, 50, ["u1", "u2"])
        rows += daily_events(a, start + timedelta(days=90), 31, 300 + i, ["u1", "u2", "u3"])
        if i < 2:  # two accounts also add users -> HIGH strength, must win the slots
            rows += [ProductEvent(a, TODAY - timedelta(days=d), "user_added", user_id=f"n{d}") for d in (2, 5, 9)]
        ctx[a] = crm(account_id=a, crm_account_ids=[f"c{i}"], owner_id="rep_1")
    wh = InMemoryWarehouse(created, rows, TODAY)
    sink = ListSink()
    r = run_daily(TODAY, wh, InMemoryCrm(ctx), SignalStore(), sink, Config(seller_capacity_per_week=3))
    assert len(sink.delivered) == 3
    delivered = {k.split(":")[0] for k, _ in sink.delivered}
    assert {"a0", "a1"} <= delivered
    held = [e for e in r.evaluations if e.decision == Decision.HOLD]
    assert len(held) == 4 and all("capacity" in e.blocking_reasons[0] for e in held)


def test_replay_keeps_capacity_slots():
    start = TODAY - timedelta(days=120)
    created, rows, ctx = {}, [], {}
    for i in range(4):
        a = f"a{i}"
        created[a] = start
        rows += daily_events(a, start, 90, 50, ["u1"]) + daily_events(a, start + timedelta(days=90), 31, 300, ["u1"])
        ctx[a] = crm(account_id=a, crm_account_ids=[f"c{i}"], owner_id="rep_1")
    wh, cr, store, sink = InMemoryWarehouse(created, rows, TODAY), InMemoryCrm(ctx), SignalStore(), ListSink()
    cfg = Config(seller_capacity_per_week=2)
    r1 = run_daily(TODAY, wh, cr, store, sink, cfg)
    r2 = run_daily(TODAY, wh, cr, store, sink, cfg)
    assert r1.counts() == r2.counts() and len(sink.delivered) == 2


def test_unanswered_signal_is_not_resent():
    store = SignalStore()
    d0 = TODAY - timedelta(days=16)
    store.record_surfaced(evaluate(feats(as_of=d0, data_loaded_through=d0), crm(), store, d0))
    ev = evaluate(feats(), crm(), store, TODAY)          # past cooldown, but still unanswered
    assert ev.decision == Decision.HOLD
    assert "still waiting on the seller" in ev.blocking_reasons[0]


def test_evaluation_is_deterministic():
    from evals.run_eval import build
    assert build() == build()
