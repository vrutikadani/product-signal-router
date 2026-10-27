from datetime import timedelta

from expansion_signals import Band, Decision, SignalStore, evaluate
from expansion_signals.ai_context import TemplateSummarizer

from helpers import TODAY, crm, feats


def test_expanding_org_surfaces():
    ev = evaluate(feats(), crm(), SignalStore(), TODAY)
    assert ev.strength == Band.HIGH
    assert ev.confidence == Band.HIGH
    assert ev.decision == Decision.SURFACE
    assert ev.actionable


def test_strength_and_actionability_are_independent():
    ev = evaluate(feats(), crm(open_opportunity=True), SignalStore(), TODAY)
    assert ev.strength == Band.HIGH          # still a strong signal
    assert ev.decision == Decision.SUPPRESS  # but someone is already on it
    assert not ev.actionable
    assert "no open opportunity" in ev.blocking_reasons


def test_single_family_is_capped():
    f = feats(users_added_30d=0, features_added_30d=0, consumption_30d=20000, consumption_prior_30d=5000)
    ev = evaluate(f, crm(), SignalStore(), TODAY)
    assert [x.family for x in ev.families if x.fired] == ["value"]
    assert ev.strength == Band.MEDIUM


def test_no_behavior_no_signal():
    f = feats(consumption_30d=1000, consumption_prior_30d=1000, users_added_30d=0, features_added_30d=0)
    assert evaluate(f, crm(), SignalStore(), TODAY).decision == Decision.NO_SIGNAL


def test_whale_lowers_confidence_not_strength():
    ev = evaluate(feats(top_user_share_30d=0.95), crm(), SignalStore(), TODAY)
    assert ev.strength == Band.HIGH
    assert ev.confidence == Band.MEDIUM
    assert any("one user" in r for r in ev.confidence_reasons)


def test_unowned_routes():
    ev = evaluate(feats(), crm(owner_id=None), SignalStore(), TODAY)
    assert ev.decision == Decision.ROUTE


def test_identity_collision_routes():
    ev = evaluate(feats(), crm(crm_account_ids=["001A", "001B"]), SignalStore(), TODAY)
    assert ev.decision == Decision.ROUTE


def test_stale_data_holds():
    ev = evaluate(feats(data_loaded_through=TODAY - timedelta(days=3)), crm(), SignalStore(), TODAY)
    assert ev.decision == Decision.HOLD
    assert ev.confidence == Band.LOW


def test_cooldown_holds():
    store = SignalStore()
    first = evaluate(feats(as_of=TODAY - timedelta(days=5)), crm(), store, TODAY - timedelta(days=5))
    store.record_surfaced(first)
    ev = evaluate(feats(), crm(), store, TODAY)
    assert ev.decision == Decision.HOLD


def test_ai_runs_only_after_surface_and_never_changes_decision():
    ev = evaluate(feats(), crm(), SignalStore(), TODAY, summarizer=TemplateSummarizer())
    assert ev.ai_context["used"] and ev.decision == Decision.SURFACE
    ev2 = evaluate(feats(), crm(open_opportunity=True), SignalStore(), TODAY, summarizer=TemplateSummarizer())
    assert ev2.ai_context["used"] is False


def test_ai_failure_is_harmless():
    class Broken:
        def summarize(self, payload):
            raise TimeoutError
    ev = evaluate(feats(), crm(), SignalStore(), TODAY, summarizer=Broken())
    assert ev.decision == Decision.SURFACE
    assert ev.ai_context["used"] is False


def test_ai_payload_has_no_raw_identifiers():
    ev = evaluate(feats(), crm(), SignalStore(), TODAY, summarizer=TemplateSummarizer())
    assert set(ev.ai_context["fields_sent"]) == {"strength", "confidence", "confidence_reasons", "families"}
