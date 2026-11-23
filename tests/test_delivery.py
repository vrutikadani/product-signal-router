"""Delivery state, backfill and pipeline trace.

Before 1.6 the signal record was written before the sink was called. A sink
error left an OPEN record that nobody saw, the next day held the account as
"open signal waiting on the seller", and one exception stopped the whole batch.
"""
from datetime import timedelta

import pytest

from product_signal_router import Decision, SignalStore
from product_signal_router.adapters import InMemoryCrm, InMemoryWarehouse, ListSink
from product_signal_router.config import Config
from product_signal_router.models import ProductEvent
from product_signal_router.pipeline import run_daily

from helpers import TODAY, crm, daily_events


def _world(accounts=("a1", "a2")):
    start = TODAY - timedelta(days=89)
    rows, created, ctx = [], {}, {}
    for a in accounts:
        rows += daily_events(a, start, 60, 60, ["u1", "u2"])
        rows += daily_events(a, start + timedelta(days=60), 30, 400, ["u1", "u2", "u3", "u4"])
        rows += [ProductEvent(a, TODAY - timedelta(days=d), "user_added", user_id=f"{a}n{d}") for d in (3, 8, 12, 20)]
        created[a] = start
        ctx[a] = crm(account_id=a, crm_account_ids=[f"crm_{a}"])
    return InMemoryWarehouse(created, rows, TODAY), InMemoryCrm(ctx)


class FlakySink:
    """Fails for chosen accounts a set number of times, then succeeds."""

    def __init__(self, fail: dict[str, int], permanent: bool = False):
        self.fail = dict(fail)
        self.permanent = permanent
        self.calls: list[str] = []
        self.delivered: list[tuple[str, object]] = []

    def deliver(self, ev, signal_key):
        self.calls.append(signal_key)
        left = self.fail.get(ev.account_id, 0)
        if left > 0:
            self.fail[ev.account_id] = left - 1
            if self.permanent:
                from product_signal_router.adapters import PermanentDeliveryError
                raise PermanentDeliveryError("owner mailbox rejected the task")
            raise ConnectionError("CRM API timed out")
        self.delivered.append((signal_key, ev))


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        return SignalStore()
    from product_signal_router.sqlite_store import SqliteSignalStore
    return SqliteSignalStore(str(tmp_path / "signals.db"))


def _calls(sink, acct):
    return [k for k in sink.calls if k.startswith(acct + ":")]


# 1. delivery ----------------------------------------------------------------

def test_sink_failure_does_not_abort_batch(store):
    from product_signal_router.state import DELIVERED, PENDING_DELIVERY
    wh, cr = _world()
    sink = FlakySink({"a1": 99})
    r = run_daily(TODAY, wh, cr, store, sink)       # must not raise
    assert [k for k, _ in sink.delivered] == [f"a2:{TODAY}"]
    assert [k for k, _ in r.failed] == [f"a1:{TODAY}"]
    assert store.get(f"a1:{TODAY}").delivery_status == PENDING_DELIVERY
    assert store.get(f"a2:{TODAY}").delivery_status == DELIVERED


def test_transient_failure_is_retried_once_on_replay(store):
    from product_signal_router.state import DELIVERED
    wh, cr = _world()
    sink = FlakySink({"a1": 1})
    run_daily(TODAY, wh, cr, store, sink)
    r2 = run_daily(TODAY, wh, cr, store, sink)
    key = f"a1:{TODAY}"
    assert _calls(sink, "a1") == [key, key]          # same idempotency key on the retry
    assert r2.delivered == [key]
    assert store.get(key).delivery_status == DELIVERED
    run_daily(TODAY, wh, cr, store, sink)            # a third replay delivers nothing
    assert len(sink.delivered) == 2


def test_persistent_failure_is_retried_exactly_once(store):
    from product_signal_router.state import DELIVERY_FAILED
    wh, cr = _world()
    sink = FlakySink({"a1": 99})
    for _ in range(3):
        run_daily(TODAY, wh, cr, store, sink)
    assert len(_calls(sink, "a1")) == 2
    assert store.get(f"a1:{TODAY}").delivery_status == DELIVERY_FAILED


def test_permanent_error_is_not_retried():
    from product_signal_router.state import DELIVERY_FAILED
    wh, cr = _world()
    store, sink = SignalStore(), FlakySink({"a1": 99}, permanent=True)
    run_daily(TODAY, wh, cr, store, sink)
    run_daily(TODAY, wh, cr, store, sink)
    assert len(_calls(sink, "a1")) == 1
    assert store.get(f"a1:{TODAY}").delivery_status == DELIVERY_FAILED


def test_undelivered_signal_does_not_block_the_next_day(store):
    wh, cr = _world()
    yday = TODAY - timedelta(days=1)
    sink = FlakySink({"a1": 99})
    run_daily(yday, wh, cr, store, sink)
    sink.fail = {}
    r = run_daily(TODAY, wh, cr, store, sink)
    a1 = [e for e in r.evaluations if e.account_id == "a1"][0]
    assert a1.decision == Decision.SURFACE, a1.blocking_reasons
    assert f"a1:{TODAY}" in r.delivered


# 2. out-of-order backfill ---------------------------------------------------

def test_backfill_after_dismissal_does_not_redeliver():
    wh, cr = _world(("a1",))
    store, sink = SignalStore(), ListSink()
    run_daily(TODAY, wh, cr, store, sink)
    store.dismiss(f"a1:{TODAY}", TODAY, "not_a_fit")
    back = TODAY - timedelta(days=5)
    r = run_daily(back, wh, cr, store, sink)
    assert len(sink.delivered) == 1
    ev = r.evaluations[0]
    assert ev.decision == Decision.SUPPRESS
    assert any("dismissed" in b for b in ev.blocking_reasons)


def test_backfill_records_but_never_delivers():
    from product_signal_router.state import NOT_DELIVERED_BACKFILL
    wh, cr = _world()
    only_a1 = InMemoryWarehouse({"a1": wh.created["a1"]}, [e for e in wh.rows if e.account_id == "a1"], TODAY)
    store, sink = SignalStore(), ListSink()
    run_daily(TODAY, only_a1, cr, store, sink)
    back = TODAY - timedelta(days=5)
    r = run_daily(back, wh, cr, store, sink)        # a2 arrives late and is backfilled
    assert r.backfill
    assert len(sink.delivered) == 1 and r.delivered == []
    a2 = [e for e in r.evaluations if e.account_id == "a2"][0]
    assert a2.decision == Decision.SURFACE
    assert store.get(f"a2:{back}").delivery_status == NOT_DELIVERED_BACKFILL


# 5. pipeline steps are in the trace ----------------------------------------

def test_capacity_step_is_in_the_trace():
    names = tuple(f"a{i}" for i in range(4))
    wh, cr = _world(names)
    r = run_daily(TODAY, wh, cr, SignalStore(), ListSink(), Config(seller_capacity_per_week=2))
    surfaced = [e for e in r.evaluations if e.decision == Decision.SURFACE]
    held = [e for e in r.evaluations if e.decision == Decision.HOLD]
    assert len(surfaced) == 2 and len(held) == 2
    for e in surfaced:
        assert any("capacity" in p for p in e.rules_passed) and not e.blocking_reasons
    for e in held:
        assert "capacity" in e.blocking_reasons[0]
        assert not any("capacity" in p for p in e.rules_passed)


def test_crm_merge_step_is_in_the_trace():
    wh, _ = _world(("a1", "a2"))
    cr = InMemoryCrm({"a1": crm(account_id="a1"), "a2": crm(account_id="a2")})   # both -> 001A
    r = run_daily(TODAY, wh, cr, SignalStore(), ListSink())
    win = [e for e in r.evaluations if e.decision == Decision.SURFACE][0]
    lose = [e for e in r.evaluations if e.decision == Decision.SUPPRESS][0]
    assert any("one signal per CRM account" in p for p in win.rules_passed)
    assert lose.blocking_reasons[0].startswith("merged into")
    assert not any("one signal per CRM account" in p for p in lose.rules_passed)
