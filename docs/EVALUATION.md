# Evaluation

`python evals/run_eval.py` replays the daily job over synthetic history and writes [`evals/REPORT.md`](../evals/REPORT.md) and `evals/report.json`. CI regenerates the report and fails if the committed copy is out of date. The demo's **Evaluation** tab reads the same JSON.

## What is being measured

| Metric | Definition | Why this definition |
|---|---|---|
| **Precision (60d)** | Delivered signals where the account expanded within 60 days after delivery, divided by delivered signals | Counted per signal, not per account, because each signal costs a seller time |
| **Recall (60d)** | Expansions surfaced at least once in the 60 days before they happened, divided by *eligible* expansions | |
| Eligible expansion | Expanded inside the window, and Sales didn't already have an open opportunity | Expansions Sales was already working aren't misses; the system is meant to find the ones nobody was on |
| **Lead time** | Days from the first signal to the expansion | A signal that arrives after the expansion is worthless, however accurate |
| **Miss cause** | For every missed expansion, the most specific reason: routed, held, suppressed, weak, or no behavior change (split by whether the account is in the top 10% by consumption) | A miss you can't explain can't be fixed |

## The synthetic world

- 400 accounts drawn from seven archetypes ([`synthetic.py`](../expansion_signals/synthetic.py)): org expander, breadth adopter, whale, one-transaction spike, steady large, slow grower, flat small.
- CRM: about 6% never matched, 5% unowned, 3% with a departed owner, 8% domain-matched, 8% with an open opportunity. Two pairs of product accounts share one CRM account. About 12% of owned accounts receive an unanswered sequence step.
- Simulated sellers respond 1 to 4 days after delivery. They work most signals on accounts that are about to expand, dismiss about half of the rest as "not a fit", snooze some, and ignore the remainder.
- Two warehouse partitions (June 10 and 11) arrive late.

**The limit of this.** I wrote the archetypes and their expansion probabilities, so the replay can't tell you whether these signals predict expansion anywhere real. What it does show is that the measurement works: misses get explained, replay is deterministic, late data is held, dismissals stick, and threshold changes show up as precision and recall moving in opposite directions. It also found real bugs (see the [postmortem](FAILURE_POSTMORTEM.md)).

## Results by logic version

| Logic | Change | Delivered | Precision | Recall | Notes |
|---|---|---|---|---|---|
| 1.1.0 | Adversarial fixes | 89 | 0.169 | 0.237 | 17 signals to steady large accounts on their onboarding ramp |
| 1.2.0 | No velocity under 60 days; open episodes not resent | 63 | 0.175 | 0.158 | Recall falls: two "catches" had been accidental |
| (sensitivity) | One family is enough | 175 | 0.171 | 0.421 | Same precision, 2.7x recall |
| 1.3.0 | One family = MEDIUM; weekly seller capacity | 165 | 0.176 | 0.421 | 30 signals to flat small accounts: signup-week users counted |
| **1.4.0** | Exclude signup week from adoption and breadth | **112** | **0.214** | **0.342** | Current |

The 1.1.0 and 1.4.0 reports are saved in [`docs/evidence/`](evidence/). The rows in between were observed during development; they weren't committed separately, so they can't be regenerated from history.

## Current misses (1.4.0)

| Cause | Count | What would address it |
|---|---|---|
| No behavior change | 13 | Nothing in product data; these may be sales-led or driven by outside events |
| Large steady account, no change to detect | 8 | The coverage audit (below) |
| Ownership or identity unresolved | 4 | Routing. These are real signals that nobody could act on |

## Coverage audit

Signals detect *change*. A large account that is already large and stays flat never fires, and those are often the accounts with the most room to expand.

The coverage audit lists the top 10% of accounts by 30-day consumption that no signal surfaced and that have no open opportunity or two-way seller conversation. It needs no outcome labels, so it can run from the first week. It is a weekly review for RevOps or sales leadership, not a seller alert. In the replay it lists 33 accounts and contains all 8 large steady accounts the signals missed. As noted above, that's partly by construction.

## What a real evaluation needs

1. **A backtest on history** before launch: run the logic over the last 6 to 12 months and look at how many accounts would have surfaced each week, how many were already being worked, lead time to the outcome, and how much volume each threshold produces.
2. **An outcome definition agreed with Sales** (an opportunity created? closed-won expansion? plan upgrade?) and a fixed window.
3. **Shadow mode**, then a pilot with a randomized delayed-treatment cohort. A fraction of qualifying accounts get their signal after a delay, and organic outreach is never blocked. Without a comparison group, "accounts we surfaced expanded" says nothing about whether surfacing them helped.
4. **Disposition coverage.** Precision computed only from signals sellers responded to is biased. Track the share of signals that receive any response, and treat silent expiry as its own signal.
