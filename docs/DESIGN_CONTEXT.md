# Design context

This repo is a daily batch job that turns account-level product usage into seller action.
It scores each account on three signal families, keeps a separate confidence band, and runs actionability rules that can block a strong signal.
It then sends at most one signal per account episode to the CRM owner and reads seller feedback the next day.
In the GTM stack it covers product-led signals: the step between warehouse data and a routed seller task.
It runs on synthetic data.
There is no real warehouse, CRM or model in this repo.

## Where it sits in the four GTM engineering layers

The four layers come from Everett Berry's (Clay) talk "GTM Engineering: The Technical Bits."

| Layer | What this repo implements |
|---|---|
| Data | `features.py:compute_features` rolls events into one feature row per account per day and drops duplicate warehouse rows on `event_id`. The CRM side is a read-only `CrmContext` from `adapters.py:CrmSource`. `evaluation.py:AsOfCrm` replays the CRM as of each day for the replay. There is no enrichment and no entity resolution: the product-to-CRM match arrives as input (`crm_account_ids`). Refresh is once a day, matching the warehouse. |
| Orchestration | `pipeline.py:run_daily` builds features and calls `pipeline.py:run_batch`. The run is idempotent on account-day (`state.py:BaseSignalStore.key_for`). A day older than the latest processed day is a backfill: recorded, never sent. `SqliteSignalStore` keeps state across restarts. There is no scheduler and no fan-out. |
| Agents | Very thin. `ai_context.py:attach_context` runs only after a SURFACE decision, sends only aggregate fields (`ALLOWED_FIELDS`), and leaves the decision alone if the summarizer fails. The only summarizer shipped is `TemplateSummarizer`, which is not a model. There is no per-account agent and no agent memory. |
| Execution | `pipeline.py:_record_and_deliver` records a signal as pending, calls `SellerSink.deliver` with the account-day key, and marks it delivered only when the call returns. `pipeline.py:_one_signal_per_crm_account` and `_apply_seller_capacity` limit volume per seller. `actionability.py:check_rules` suppresses on an open opportunity or a recent two-way conversation. The sink is an in-memory list (`adapters.py:ListSink`). |

Berry's points that show up here:

- **Updates cost money, so refresh on purpose.** The job runs on the warehouse's daily cadence.
  If the day's partition is missing, `check_rules` holds the account instead of deciding on a partial window.
- **Multi-channel suppression.** An open opportunity or a real conversation suppresses a signal, even the strongest one.
  A one-way sequence email does not; it becomes a note for the seller.
- **The human-agent interface.** Every decision carries the rules it passed and the reason it stopped.
  Sellers can act, snooze or dismiss (`state.py:action`, `snooze`, `dismiss`), and the next run reads it.

Berry's points it does not cover: waterfall enrichment, entity resolution, keeping many tools in sync, agent-written CRM fields, and deliverability.

## Comparison with LangChain's published GTM agents

- **"How we built LangChain's GTM Agent": trigger, check whether to reach out, gather context, draft for the rep.** Different.
  Here the trigger is a daily batch, not a new lead, and the output is a routed signal with evidence, not a drafted message.
  The check is `check_rules`, in a fixed order.
  There is no draft and no rep approval step, because no message is sent to a customer.
- **Same post: learn rep style from rep edits, with per-rep observations.** Not done.
  Memory here is about outcomes and volume: dismissals with a reason and snoozes per account, and a weekly cap per owner.
  There is nothing to learn style from, because nothing is written for the customer.
- **"How we built LangChain's Paid Media Agent": model for judgment, code for computation, date windows and matching.** Same, taken further.
  Windows, thresholds and the CRM merge are code, and no model makes any decision.
  The summarizer may only explain a decision that is already made.
- **Same post: a source of truth per metric, and keep uncertainty when data can't be joined.** Same idea.
  Strength and confidence are separate bands (`signals.py:evaluate_strength`, `evaluate_confidence`).
  No CRM match, several matches, or an inactive owner returns ROUTE with the reason instead of a guess.
- **Same post: a path from analysis to action with post-apply checks.** Partly.
  Delivery is recorded as pending, sent, then delivered, and a failure is retried once on replay.
  There is no human approval before delivery and no read-back after the sink returns.
- **"How We Built Our Design Agent": hard ordering in code, fast and slow feedback loops.** Same idea.
  Rule order is fixed in `check_rules`.
  The fast loop is a dismissal read on the next run.
  The slow loop is the replay harness (`evaluation.py:replay`, `measure`, `coverage_audit`), which found bugs the unit tests missed.

This repo does not use LangGraph, Deep Agents or LangSmith.
The engine imports only the standard library, and CI checks that.
The decision is a pure function of the day's features, the CRM snapshot and the store, so a replay is just calling it again.
The hard problems are side effects, identity and idempotency: one signal per account-day, one per CRM account, and no re-send on backfill.
A framework does not solve those on its own.

## Not built here

- No model client.
  Only `TemplateSummarizer` ships; any object with `summarize(payload)` can replace it.
- No per-rep learned memory. Per-account dismissals and snoozes and the per-owner cap are the only feedback state.
- No tracing or LangSmith.
  The trace is `rules_passed` and `blocking_reasons` on each `Evaluation`.
- No live integrations.
  The warehouse, CRM and sink are in-memory adapters.
- No identity matching between product and CRM accounts.
- No ownership or review queue for ROUTE decisions.
- No scheduler, shadow mode, kill switch or circuit breaker.
  These are designed in `docs/ARCHITECTURE.md`, not built.
- No fitted thresholds and no backtest on real history.
- Single process.

These comparisons are my reading of public posts; they are not claims about how those systems work internally beyond what the posts say.
