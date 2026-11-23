# Interview walkthrough

A script for explaining this repo out loud. Times are rough. File references are `file:function`.

## 30 seconds: context

Product usage data says an account is growing. Sales wants to know which accounts to call. Joining the two is easy. Deciding which ones deserve a seller's time is the hard part. This repo is a daily batch job that scores each account, checks whether a seller can act on it, and sends at most one signal per account episode to the CRM owner. It runs on synthetic data. It is a reference design, not a production system.

## 90 seconds: architecture

- **Input.** Warehouse events roll up to one feature row per account per day (`features.py:compute_features`). The CRM gives a read-only snapshot: the CRM match, owner, open opportunity, last activity.
- **Engine.** For one account and one day (`engine.py:evaluate`):
  - strength from three signal families (`signals.py:evaluate_strength`)
  - confidence, kept separate (`signals.py:evaluate_confidence`)
  - actionability rules that can block a strong signal (`actionability.py:check_rules`)
- **Run-level steps.** Across all accounts that day (`pipeline.py:run_batch`): one signal per CRM account, then a weekly cap per owner.
- **Delivery.** Record as pending, call the sink, mark delivered only when it returns (`pipeline.py:_record_and_deliver`).
- **Memory.** A signal store, one row per account-day, in memory or SQLite (`state.py`, `sqlite_store.py`). Seller feedback is written back to it and read the next day.
- **Measurement.** A replay over 11 weeks of synthetic history, with precision, recall and a miss audit (`evaluation.py:replay`, `evaluation.py:measure`).

## 5 minutes: one account, warehouse rows to seller card

1. **Rows.** `adapters.py:InMemoryWarehouse.events` returns the account's events up to the day. Rows are deduplicated on `event_id` (`features.py:compute_features`).
2. **Features.** Two trailing 30-day windows. Consumption, users added, features added, plus shape checks: largest single event share and top user share. Users and features from signup week don't count as added.
3. **Strength.** `signals.py:value_family`, `adoption_family`, `breadth_family`. One family firing is MEDIUM. Two or more is HIGH. Value needs 60 days of history, because velocity on a new account is mostly onboarding.
4. **Confidence.** `signals.py:evaluate_confidence`. Short history, one dominant user or a domain-only CRM match lowers it. It describes trust. It does not block anything except stale data.
5. **Actionability.** `actionability.py:check_rules`, in order: exactly one CRM match, an owner, an active owner, no open opportunity, no two-way conversation in 14 days, fresh data, then memory (dismissal, open episode, expiry, cooldown, snooze). The first failing rule picks ROUTE, SUPPRESS or HOLD. All rules are reported.
6. **Decision.** `engine.py:evaluate` turns strength, rules and confidence into one of SURFACE, HOLD, SUPPRESS, ROUTE, NO_SIGNAL. If the decision is SURFACE, an optional summarizer writes a note from aggregates only (`ai_context.py:attach_context`).
7. **Merge.** `pipeline.py:_one_signal_per_crm_account`. Two product accounts on one CRM account: one wins, the other is SUPPRESS with "merged into".
8. **Capacity.** `pipeline.py:_apply_seller_capacity`. Each owner gets at most 5 new signals per 7 days. Overflow is HOLD with the reason. Both steps add a line to the trace.
9. **Record and deliver.** `state.py:BaseSignalStore.record_surfaced` writes `PENDING_DELIVERY`. `adapters.py:SellerSink.deliver` gets the evaluation and the key `account_id:as_of`. `state.py:mark_delivered` runs only after it returns.
10. **Seller card.** The sink would write a CRM task. The demo shows the same evaluation in the Signal Inspector: evidence, every rule, AI input, decision, history.
11. **Feedback.** `state.py:action`, `snooze`, `dismiss`. The next evaluation reads it through `state.py:last_delivered`.

## Why a strong signal does not justify outreach on its own

Strength says "something changed". It does not say "a seller should call today". Point at `actionability.py:check_rules`:

- An open opportunity or a recent two-way conversation means someone is already on it. A second seller is noise. (`test_strength_and_actionability_are_independent`, `test_real_conversation_still_suppresses`)
- No owner, an inactive owner or two CRM matches means nobody can act. Guessing sends it to the wrong person. (`test_unowned_routes`, `test_inactive_owner_routes`, `test_identity_collision_routes`)
- A seller who said "not a fit" two weeks ago should not hear it again unless something got stronger. (`test_dismissal_respected_after_cooldown`, `test_dismissal_lifts_when_strength_rises`)
- An unanswered signal is still open. Sending another on top of it trains sellers to ignore the feed. (`test_unanswered_signal_is_not_resent`)

The demo's Atlas Software scenario shows this: the strongest signal in the set, suppressed because there is an open opportunity.

## Data, Orchestration, Agents, Execution

| Layer | In this repo | Not in this repo |
|---|---|---|
| Data | `features.py`, warehouse and CRM protocols in `adapters.py`, `AsOfCrm` for replay | Real connectors, identity matching |
| Orchestration | `pipeline.py:run_daily` / `run_batch`: daily, idempotent on account-day, outbox delivery, backfill mode | A scheduler (cron, Airflow) to call it |
| Agents | `ai_context.py`: an optional summarizer after the decision. It never decides | Any model that picks accounts or acts on its own |
| Execution | `SellerSink.deliver` with an idempotency key; feedback written back to the store | A real CRM task writer, an ownership queue, a review UI |

The agent layer is thin on purpose. Rules decide, so every decision can be explained and replayed.

## Why a daily idempotent batch, not a durable agent framework

- The warehouse refreshes once a day. Nothing upstream changes faster than that.
- The decision is a pure function of the day's features, the CRM snapshot and the store. Replay means calling it again. No workflow history is needed.
- The only state that must survive is small: one row per account-day, with delivery and seller status. A table holds it. `SqliteSignalStore` shows that.
- The one side effect is delivery. An outbox record plus an idempotency key covers it. Delivery is at-least-once; the key makes a repeat safe.
- A durable workflow engine adds workers, a runtime and versioned workflow code. That pays off for long-running steps, timers or multi-step tool calls. None of those exist here.
- When that changes, the batch can stay the decision step. Example: waiting days for a seller reply with escalation timers. A workflow engine would wrap it.

## Five hard follow-ups

**1. What if the CRM call fails halfway through the batch?**
That account's record stays pending with the error. The batch continues. Replaying the day retries once with the same key, then gives up. A pending record is not memory, so the next day is not blocked. `test_sink_failure_does_not_abort_batch`, `test_transient_failure_is_retried_once_on_replay`, `test_persistent_failure_is_retried_exactly_once`, `test_permanent_error_is_not_retried`, `test_undelivered_signal_does_not_block_the_next_day`.

**2. What happens when you re-run last Tuesday?**
Same decisions, nothing new delivered. Memory skips the evaluated day's own record and reads every other day, so a later dismissal still counts. A day older than the latest processed day is a backfill: recorded, never sent. `test_replay_is_idempotent`, `test_backfill_after_dismissal_does_not_redeliver`, `test_backfill_records_but_never_delivers`, `test_rerun_under_new_logic_version_does_not_open_second_signal`.

**3. How do you know the replay numbers are not flattered?**
Simulated sellers don't see outcomes. Before 1.5 they did, and that made dismissals look free. The CRM is replayed as of each day for activity, opportunity and owner. Identity has no history, and EVALUATION.md says which way each leak would bias a real replay. The outcomes come from archetypes I wrote, so the numbers show the measurement works, not that the signals predict. `test_evaluation_is_deterministic`, `test_asof_crm_hides_future_opportunity_and_owner_change`.

**4. Two workspaces for one company, one seller. What do they get?**
One signal. The winner is chosen by strength, confidence, then account ID, so a replay picks the same one. The trace says why. `test_two_product_accounts_one_crm_account_surface_once`, `test_crm_merge_step_is_in_the_trace`.

**5. What if the job restarts or the box dies?**
On `SqliteSignalStore`, episodes, dismissals, delivery state and processed days survive. A replay after restart delivers nothing new. One row per account-day is a database constraint. `test_sqlite_store_keeps_memory_across_restart`, `test_sqlite_store_has_one_row_per_account_day`.

## Verified vs proposed

**Verified by tests in this repo (57 tests, CI on Python 3.11 to 3.13):**
strength and confidence rules; actionability rules; dismissal windows; episode expiry; CRM merge; seller capacity; delivery state and one retry; backfill without delivery; SQLite restart; as-of CRM for opportunity and owner; replay determinism; the evaluation report is current; engine is standard library only.

**Proposed, designed in ARCHITECTURE.md, not built:**
real warehouse and CRM connectors; identity matching; an ownership and review queue; a scheduler; shadow mode; a kill switch; a silent-expiry circuit breaker; fitted thresholds; a backtest on real history.

**What the demo runs:** `demo_api.run` calls `pipeline.run_batch` for one account with an in-memory store. It skips `features.compute_features` because the form holds feature values.
