import json

from product_signal_router import demo_api

BASE = dict(account="t", account_age_days=120, consumption_30d=18000, velocity=2.6, top_user_share=.25,
            users_total=14, users_added_30d=6, features_total=4, features_added_30d=2,
            open_opportunity=False, ownership="resolved", activity="none")


def run(**kw):
    return json.loads(demo_api.run(json.dumps({**BASE, **kw})))


def setup_function():
    demo_api.reset()


def test_feedback_loop():
    assert run()["decision"] == "SURFACE"
    demo_api.feedback("t", "dismiss", "not_a_fit")
    demo_api.advance("t", 20)
    assert run()["decision"] == "SUPPRESS"
    demo_api.advance("t", 95)
    assert run()["decision"] == "SURFACE"


def test_every_ownership_option_runs():
    for k in demo_api.OWNERSHIP:
        demo_api.reset()
        assert run(ownership=k)["decision"] in {"SURFACE", "ROUTE"}


def test_coverage_flag_only_on_no_signal():
    assert run(velocity=1.0, users_added_30d=0, features_added_30d=0, consumption_30d=65000,
               coverage_cutoff=12000)["coverage_flag"]
    demo_api.reset()
    assert not run(coverage_cutoff=12000)["coverage_flag"]
