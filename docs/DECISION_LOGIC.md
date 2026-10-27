# Decision logic

Logic version **1.5.0**. Every threshold lives in [`expansion_signals/config.py`](../expansion_signals/config.py). None of them are fitted; they are starting points for a backtest to replace.

The engine answers three separate questions and only then makes a decision:

| Question | Output | Changes the decision? |
|---|---|---|
| How much expansion behavior is there? | **Signal strength**: NONE / LOW / MEDIUM / HIGH | Yes: NONE never surfaces |
| How far should we trust that reading? | **Confidence**: LOW / MEDIUM / HIGH, with reasons | Only LOW blocks. Otherwise it ranks signals for capacity and is shown to the seller |
| Can this owner usefully act today? | **Actionability**: rules passed and blocking reasons | Yes: any blocking rule stops the signal |

Keeping these apart is the point. Atlas Software in the demo is HIGH strength, HIGH confidence and not actionable, because a seller already has an open opportunity there. A single score would rank it at the top of the list.

## 1. Signal strength: three families

| Product behavior (examples) | Family | What it suggests |
|---|---|---|
| Spend growth, transactions, consumption, API calls, compute | **Usage / value** | Consumption or value expansion |
| Additional users, seats, departments, admins | **Organizational adoption** | More of the organization is buying in |
| Additional cards, features, workflows, integrations | **Product breadth** | Operational embedding: harder to replace, more surface to expand |

| Family | Fires when | HIGH band when |
|---|---|---|
| Usage / value | 30-day consumption ≥ 4,000 **and** velocity (30d vs prior 30d) ≥ 2.0x **and** the account is at least 60 days old. If one event is more than 50% of the month, velocity is recomputed without it | velocity ≥ 3.0x |
| Organizational adoption | ≥ 3 users added in 30 days, not counting signup week | ≥ 6 users added |
| Product breadth | ≥ 2 features enabled in 30 days, not counting signup week | ≥ 4 features in use |

**Strength.** No family fired: NONE. One family: MEDIUM. Two or more: HIGH.

A family's own band is shown as evidence but doesn't raise strength on its own. One family moving a lot is common. Several moving together is rarer and says more.

## 2. Confidence

Confidence starts at HIGH and is lowered, with a stated reason, by:

| Condition | Lowers to |
|---|---|
| Warehouse partition for the day is missing | LOW |
| Under 45 days of account history | MEDIUM |
| More than 80% of consumption from one user | MEDIUM |
| CRM account matched by domain, not by ID | MEDIUM |

Only LOW blocks a signal, and in 1.5 the only source of LOW is stale data, which the freshness rule already holds. So confidence doesn't decide anything on its own. It orders signals when an owner is over capacity, and it tells the seller how far to trust what they're reading. That's deliberate: an earlier version relied on MEDIUM confidence to stop bad signals, and it didn't (see the postmortem). Anything that must stop a signal is a rule in strength or actionability.

## 3. Actionability

Rules run in this order. The first failure decides the outcome; every rule is still reported.

| Rule | If it fails |
|---|---|
| Identity resolves to exactly one CRM account | ROUTE (identity review) |
| The account has an owner | ROUTE |
| The owner is an active user | ROUTE |
| No open opportunity | SUPPRESS |
| No two-way conversation (meeting, reply, connected call, demo) in the last 14 days. Unknown activity types count as two-way | SUPPRESS |
| Warehouse partition for the day is loaded (no gap) | HOLD |
| Seller feedback on the last signal (below) | SUPPRESS, ROUTE or HOLD |
| The last signal isn't still open and unanswered (21-day expiry) | HOLD |
| An expired, unanswered signal stays quiet until 90 days after it was sent, unless strength rises | SUPPRESS |
| Not surfaced in the last 14 days | HOLD |
| Not snoozed | HOLD |

One-way touches (a sequence step, an email sent, a call attempt) don't suppress. They are passed to the seller as a note: *"sequence step sent 4 days ago with no reply logged"*.

Engine memory reads only signals from **earlier days**, and looks across all product accounts that share the CRM account. That makes replaying a day give the same answer.

## 4. Decision

```
strength NONE                -> NO_SIGNAL
any blocking rule            -> ROUTE / SUPPRESS / HOLD (first one wins)
confidence LOW               -> HOLD
otherwise                    -> SURFACE
then, across the whole run:
  several product accounts on one CRM account -> one SURFACE, the rest SUPPRESS (merged)
  owner over weekly capacity                  -> overflow HOLD, ranked by strength, confidence, families
```

`LOW` strength and `MONITOR` exist in the types, but no rule produces them in 1.5.0. Until 1.2.0, a single MEDIUM family produced LOW (monitor only). See the postmortem for why that changed.

## 5. Seller feedback

Feedback is stored with the signal record, and the next evaluation reads it.

| Seller says | Effect on later evaluations |
|---|---|
| Worked it | Normal 14-day cooldown |
| Snooze | HOLD until the snooze ends |
| Dismiss: not a fit | SUPPRESS for 90 days, **unless strength rises** above the band it was dismissed at |
| Dismiss: bad timing | SUPPRESS for 30 days, same exception |
| Dismiss: already engaged | SUPPRESS for 30 days. Also a CRM data-quality finding: the CRM didn't show the engagement |
| Dismiss: wrong owner | ROUTE until the CRM owner changes, or a reviewer clears it |
| Dismiss: bad data | HOLD until a reviewer clears it (`SignalStore.clear`). There is no review queue UI in this repo |
| No response | The signal stays the one open signal for 21 days. After that, silence counts as a soft "no": no new signal until 90 days after the first, unless strength rises. Until 1.5 an expired signal was re-sent every 21 days |

## 6. Where AI is allowed

After the decision is SURFACE, an optional summarizer may write a short note for the seller. It receives only `strength`, `confidence`, `confidence_reasons` and `families`: aggregates already on the evaluation, with no raw events and no user identities. If it is disabled, fails or times out, the decision is unchanged and the seller still sees the deterministic evidence. The inspector shows whether it ran and exactly which fields it got.

The default `TemplateSummarizer` uses no model at all. A model-backed summarizer only has to implement `summarize(payload) -> str`.
