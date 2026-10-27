"""Cases written to break the engine. Each asserts the behavior a seller needs.

See docs/FAILURE_POSTMORTEM.md for which of these failed on v1 and what changed.
"""
from datetime import timedelta

from expansion_signals import Band, Decision, SignalStore, evaluate
from expansion_signals.adapters import InMemoryCrm, InMemoryWarehouse, ListSink
from expansion_signals.models import ProductEvent
from expansion_signals.pipeline import run_daily

from helpers import TODAY, crm, daily_events, feats


# 1. outreach that nobody answered is not engagement
def test_ignored_outreach_does_not_suppress():
    c = crm(last_activity_date=TODAY - timedelta(days=4), last_activity_type="sequence_step")
    ev = evaluate(feats(), c, SignalStore(), TODAY)
    assert ev.decision == Decision.SURFACE


def test_real_conversation_still_suppresses():
    c = crm(last_activity_date=TODAY - timedelta(days=4), last_activity_type="meeting")
    assert evaluate(feats(), c, SignalStore(), TODAY).decision == Decision.SUPPRESS


# 2. an owner who has left the company cannot act
def test_inactive_owner_routes():
    ev = evaluate(feats(), crm(owner_active=False), SignalStore(), TODAY)
    assert ev.decision == Decision.ROUTE


# 3. replaying a day must give the same answer, and deliver nothing new
def _world():
    start = TODAY - timedelta(days=89)
    rows = (daily_events("a1", start, 60, 60, ["u1", "u2"]) +
            daily_events("a1", start + timedelta(days=60), 30, 400, ["u1", "u2", "u3", "u4"]))
    rows += [ProductEvent("a1", TODAY - timedelta(days=d), "user_added", user_id=f"n{d}") for d in (3, 8, 12, 20)]
    wh = InMemoryWarehouse({"a1": start}, rows, TODAY)
    return wh, InMemoryCrm({"a1": crm(account_id="a1")})


def test_replay_is_idempotent():
    wh, cr = _world()
    store, sink = SignalStore(), ListSink()
    r1 = run_daily(TODAY, wh, cr, store, sink)
    r2 = run_daily(TODAY, wh, cr, store, sink)
    assert len(sink.delivered) == 1
    assert r1.counts() == r2.counts()


# 4. one large transaction is not a trend
def test_single_transaction_spike_is_not_high_confidence():
    f = feats(consumption_30d=16000, consumption_prior_30d=3000, largest_event_share_30d=0.85,
              users_added_30d=0, features_added_30d=0)
    ev = evaluate(f, crm(), SignalStore(), TODAY)
    assert ev.decision != Decision.SURFACE or ev.confidence != Band.HIGH


# 5. two product accounts that resolve to the same CRM account
def test_two_product_accounts_one_crm_account_surface_once():
    wh, _ = _world()
    rows = list(wh.rows) + [ProductEvent("a2", e.day, e.kind, e.user_id, e.amount) for e in wh.rows]
    wh2 = InMemoryWarehouse({"a1": wh.created["a1"], "a2": wh.created["a1"]}, rows, TODAY)
    cr = InMemoryCrm({"a1": crm(account_id="a1"), "a2": crm(account_id="a2")})  # both -> 001A
    sink = ListSink()
    run_daily(TODAY, wh2, cr, SignalStore(), sink)
    assert len(sink.delivered) == 1


# 6. a seller's "not a fit" must survive the next evaluation
def test_dismissal_respected_after_cooldown():
    store = SignalStore()
    d0 = TODAY - timedelta(days=20)
    first = evaluate(feats(as_of=d0, data_loaded_through=d0), crm(), store, d0)
    rec, _ = store.record_surfaced(first)
    store.dismiss(rec.key, d0, "not_a_fit")
    ev = evaluate(feats(), crm(), store, TODAY)   # same behavior, 20 days later
    assert ev.decision == Decision.SUPPRESS


# 7. a large, steady account never fires an expansion signal
def test_large_steady_account_is_invisible_to_signals():
    f = feats(consumption_30d=60000, consumption_prior_30d=58000, users_added_30d=1, features_added_30d=0,
              users_total=40, active_users_30d=35)
    ev = evaluate(f, crm(), SignalStore(), TODAY)
    # This is by design (the system detects change, not size), so the miss has
    # to be caught by the false-negative audit, not by the engine.
    assert ev.decision == Decision.NO_SIGNAL


# 8. late warehouse data: hold, and do not treat a partial window as a decline
def test_late_partition_holds_instead_of_deciding():
    wh, cr = _world()
    late = InMemoryWarehouse(wh.created, [e for e in wh.rows if e.day <= TODAY - timedelta(days=2)],
                             TODAY - timedelta(days=2))
    r = run_daily(TODAY, late, cr, SignalStore(), ListSink())
    assert r.evaluations[0].decision in (Decision.HOLD, Decision.NO_SIGNAL, Decision.MONITOR)
    assert r.evaluations[0].decision != Decision.SURFACE
