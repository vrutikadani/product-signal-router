"""A durable signal store, and replay inputs that don't see the future."""
from datetime import timedelta

import pytest

from product_signal_router import Decision
from product_signal_router.adapters import ListSink
from product_signal_router.models import CrmContext
from product_signal_router.pipeline import run_daily

from helpers import TODAY
from test_delivery import _world


# 3. SQLite store ------------------------------------------------------------

def test_sqlite_store_keeps_memory_across_restart(tmp_path):
    from product_signal_router.sqlite_store import SqliteSignalStore
    path = str(tmp_path / "signals.db")
    wh, cr = _world()
    first = TODAY - timedelta(days=10)
    s1, sink = SqliteSignalStore(path), ListSink()
    run_daily(first, wh, cr, s1, sink)
    assert len(sink.delivered) == 2
    s1.dismiss(f"a1:{first}", first, "bad_data")
    s1.close()

    s2 = SqliteSignalStore(path)                    # the job restarts
    r = run_daily(TODAY, wh, cr, s2, sink)
    by = {e.account_id: e for e in r.evaluations}
    assert by["a1"].decision == Decision.HOLD and "bad data" in by["a1"].blocking_reasons[0]
    assert by["a2"].decision == Decision.HOLD and "waiting on the seller" in by["a2"].blocking_reasons[0]
    run_daily(first, wh, cr, s2, sink)              # replaying the old day after a restart
    assert len(sink.delivered) == 2


def test_sqlite_store_has_one_row_per_account_day(tmp_path):
    import sqlite3
    from product_signal_router.sqlite_store import SqliteSignalStore
    wh, cr = _world(("a1",))
    s = SqliteSignalStore(str(tmp_path / "signals.db"))
    r = run_daily(TODAY, wh, cr, s, ListSink())
    _, created = s.record_surfaced(r.evaluations[0])
    assert created is False
    with pytest.raises(sqlite3.IntegrityError):
        s.conn.execute("INSERT INTO signals (key, account_id, as_of, logic_version, strength, confidence) "
                       "VALUES ('x', 'a1', ?, '0', 'HIGH', 'HIGH')", (TODAY.isoformat(),))


# 4. replay CRM as of the replay day -----------------------------------------

def test_asof_crm_hides_future_opportunity_and_owner_change():
    from product_signal_router.evaluation import AsOfCrm
    from product_signal_router.synthetic import SyntheticAccount
    d = TODAY - timedelta(days=30)
    acct = SyntheticAccount(
        "a1", "org_adopter", d - timedelta(days=90), d, None,
        CrmContext("a1", ["c1"], "explicit_id", owner_id="rep_2", open_opportunity=True),
        opportunity_opened_on=d + timedelta(days=10),
        owner_history=[(d - timedelta(days=90), "rep_1", True), (d + timedelta(days=5), "rep_2", True)],
    )
    view = AsOfCrm({"a1": acct})
    view.day = d
    ctx = view.context("a1")
    assert ctx.open_opportunity is False and ctx.owner_id == "rep_1"
    view.day = d + timedelta(days=10)
    ctx = view.context("a1")
    assert ctx.open_opportunity is True and ctx.owner_id == "rep_2"
