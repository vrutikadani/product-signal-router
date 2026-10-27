# Failure postmortem

Two rounds of testing broke the first version. Unit tests written against the design passed. Cases written to break it did not, and a replay over synthetic history found problems neither set of tests had covered.

Evidence in this repository:

- [`docs/evidence/v1_adversarial_run.txt`](evidence/v1_adversarial_run.txt): the adversarial run against v1, 6 failed and 3 passed
- [`docs/evidence/eval_before_history_and_episode_fix.md`](evidence/eval_before_history_and_episode_fix.md): the replay report on logic 1.1.0
- [`docs/evidence/eval_after.md`](evidence/eval_after.md): the same replay on 1.4.0
- the commit history: `Engine v1`, then `Add adversarial cases (6 of 9 fail on v1)`, then the fixes

## The main failure: onboarding looked like expansion, and the safeguard did nothing

**What happened.** On logic 1.1.0 the replay sent 17 signals to accounts in the *steady large* archetype. These accounts are big from day one and never grow. All 17 fired when the account was 30 to 38 days old.

**Why.** Consumption velocity compares the last 30 days with the 30 days before them. At day 35, most of the "prior" window falls before the account existed, so velocity came out between 3x and 46x. A large account in its second month looked like the fastest-growing account in the book.

**Why the safeguard didn't help.** v1 already knew short history was a problem. Accounts under 45 days old had confidence lowered to MEDIUM. But MEDIUM confidence still surfaces, so the rule changed a label in the inspector and nothing a seller saw. The design had treated confidence as a control, and it isn't one: it describes trust, while the decision rules decide.

**Fix (1.2.0).** The value family does not fire for accounts under 60 days old. Two full windows are needed before velocity means anything. Young accounts can still fire on adoption or breadth, because a new account adding seven people after signup week is a real early signal (the *Early organizational adoption* scenario in the demo).

**The same mistake, twice more.**

- The one-transaction spike had the same shape. v1 computed `largest_event_share_30d` and then never used it. The fix recomputes velocity without the dominant event; if the growth disappears, the value family doesn't fire.
- Once the velocity fix was in, the replay showed 30 signals going to *flat small* accounts. Users and features created in signup week were being counted as "added in the last 30 days". Setup done in the first 7 days of an account's life is now excluded from adoption and breadth (1.4.0).

**What it cost in recall.** Recall *fell* after the fix, from 0.237 to 0.158. Two of the "caught" expansions had been steady large accounts surfaced by accident during onboarding. Removing the false positive also removed a lucky true positive. The honest number was lower, and the large steady accounts now show up where they belong: as false negatives.

## The second failure: a quiet system that looked careful

v1 required two signal families before a signal was HIGH. A single MEDIUM family was LOW, meaning "monitor, no seller time". The idea was that corroboration buys precision.

The replay disagreed. Letting any single family surface at MEDIUM:

| Rule | Signals delivered | Precision (60d) | Recall (60d) |
|---|---|---|---|
| Two families required (1.2.0) | 63 | 0.175 | 0.158 |
| One family is enough (sensitivity run) | 175 | 0.171 | 0.421 |

Precision did not move. The rule was cutting volume, not noise, and it hid most of the expansions. A system tuned like this looks disciplined (few alerts, few dismissals) while it misses most of the accounts that matter.

**Caveat.** These outcomes come from synthetic archetypes I wrote, so this table doesn't prove that corroboration fails to help in real data. It shows that "two families" was a belief, not a measured result, and that its effect on recall was large.

**Fix (1.3.0).** One family is MEDIUM strength and can surface. Volume became an explicit step: each owner gets at most N new signals per rolling week (default 5, meant to be set from a capacity review), ranked by strength, then confidence, then number of families. Overflow is held with a visible reason, not delivered and ignored. With the onboarding fix also in, 1.4.0 delivers 112 signals at precision 0.214 and recall 0.342.

## The adversarial cases that failed on v1

| # | Case | v1 behavior | Fix |
|---|---|---|---|
| 1 | Sequence email sent 4 days ago, no reply | Suppressed as "already engaged" | Only two-way activity (meeting, reply, connected call) suppresses. One-way touches become a note for the seller. Unknown activity types still suppress |
| 2 | CRM owner has left the company | Surfaced to the departed owner | ROUTE when the owner is inactive, not only when missing. `owner_active` was in the v1 data model and never checked |
| 3 | Same day run twice | Second run returned HOLD (cooldown) for the signal the first run had just created, so run-level counts changed on replay | Engine memory only reads signals from earlier days |
| 4 | One transaction is 85% of the month | Surfaced at HIGH confidence | Velocity recomputed without the dominant event |
| 5 | Two product accounts resolve to one CRM account | Two signals for one seller on one account | One signal per CRM account per day, with a deterministic winner so a replay picks the same one |
| 6 | Seller dismissed as "not a fit", same behavior 20 days later | Surfaced again | Dismissals are read on re-evaluation, with reason-specific windows (below) |

Three cases passed on v1: a real meeting still suppresses, late warehouse data holds rather than decides, and a large flat account produces NO_SIGNAL. The last one passing is the problem the coverage audit exists for.

### A fix that created a bug

Fix 3 (read only earlier days) broke something that had worked by accident. The v1 signal key was `account:day:logic_version`. v1 never duplicated on a re-run under new logic, because its cooldown counted the same-day record. Once same-day records stopped counting, a re-run under a new logic version would open a second signal for the same account-day. The key is now `account:day`, and `test_rerun_under_new_logic_version_does_not_open_second_signal` covers it. I confirmed the test fails with the old key.

## What I'd watch for next

- **Velocity is late.** In the replay, many *org expander* accounts expanded before the 30-vs-30 velocity crossed 2x. Median lead time is 16 days. A shorter window (14 vs 14) or a slope over weekly buckets might fire earlier, at the cost of more noise. That should be backtested, not assumed.
- **Capacity barely binds in the replay** (1 account-day held). With 12 owners and 400 accounts that is expected. In production, capacity needs a real number from Sales before launch.
- **The coverage audit is circular here.** It catches 8 of 8 missed large steady accounts because the synthetic large steady accounts are, by construction, the top decile. On real data its hit rate is unknown.
