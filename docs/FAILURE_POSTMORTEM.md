# Failure postmortem

Three rounds of testing broke what I'd built. Unit tests written against the design passed. Cases written to break it did not. A replay over synthetic history found problems neither set of tests had covered. An independent review of the finished version then found three more bugs and a flaw in the evaluation itself.

Evidence in this repository:

- [`docs/evidence/v1_adversarial_run.txt`](evidence/v1_adversarial_run.txt): the adversarial run against v1, 6 failed and 3 passed
- [`docs/evidence/eval_1.1.0.md`](evidence/eval_1.1.0.md): the replay report on logic 1.1.0
- [`docs/evidence/eval_1.4.0.md`](evidence/eval_1.4.0.md): the same replay on 1.4.0
- [`docs/evidence/review_findings_run.txt`](evidence/review_findings_run.txt): the three review cases failing on 1.4.0
- the commit history, in the order the work happened: v1, the failing adversarial cases, the fixes, the replay, then the review cases and their fixes. The project was built in one focused session, so the timestamps are close together. The commits are not reconstructed after the fact

## The main failure: onboarding looked like growth, and the safeguard did nothing

**What happened.** On logic 1.1.0 the replay sent 17 signals to accounts in the *steady large* archetype. These accounts are big from day one and never grow. All 17 fired when the account was 30 to 38 days old.

**Why.** Consumption velocity compares the last 30 days with the 30 days before them. At day 35, most of the "prior" window falls before the account existed, so velocity came out between 3x and 46x. A large account in its second month looked like the fastest-growing account in the book.

**Why the safeguard didn't help.** v1 already knew short history was a problem. Accounts under 45 days old had confidence lowered to MEDIUM. But MEDIUM confidence still surfaces, so the rule changed a label in the inspector and nothing a seller saw. The design had treated confidence as a control, and it isn't one: it describes trust, while the decision rules decide.

**Fix (1.2.0).** The value family does not fire for accounts under 60 days old. Two full windows are needed before velocity means anything. Young accounts can still fire on adoption or breadth, because a new account adding seven people after signup week is a real early signal (the *Early organizational adoption* scenario in the demo).

**The same mistake, twice more.**

- The one-transaction spike had the same shape. v1 computed `largest_event_share_30d` and then never used it. The fix recomputes velocity without the dominant event; if the growth disappears, the value family doesn't fire.
- Once the velocity fix was in, the replay showed 30 signals going to *flat small* accounts. Users and features created in signup week were being counted as "added in the last 30 days". Setup done in the first 7 days of an account's life is now excluded from adoption and breadth (1.4.0).

**What it cost in recall.** Recall *fell* after the fix, from 0.237 to 0.158. Two of the "caught" conversions had been steady large accounts surfaced by accident during onboarding. Removing the false positive also removed a lucky true positive. The honest number was lower, and the large steady accounts now show up where they belong: as false negatives.

## The second failure: a quiet system that looked careful

v1 required two signal families before a signal was HIGH. A single MEDIUM family was LOW, meaning "monitor, no seller time". The idea was that corroboration buys precision.

The replay disagreed. Letting any single family surface at MEDIUM:

| Rule | Signals delivered | Precision (60d) | Recall (60d) |
|---|---|---|---|
| Two families required (1.2.0) | 63 | 0.175 | 0.158 |
| One family is enough (sensitivity run) | 175 | 0.171 | 0.421 |

Precision did not move. The rule was cutting volume, not noise, and it hid most of the conversions. A system tuned like this looks disciplined (few alerts, few dismissals) while it misses most of the accounts that matter.

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

## Third round: an independent review of 1.4.0

I asked a reviewer with no context on the build to read the code as a Staff Engineer would and to try to break it. Three cases failed on 1.4.0 (they're at the bottom of `tests/test_adversarial.py`):

| Case | 1.4.0 behavior | Fix (1.5.0) |
|---|---|---|
| A signal nobody answers | Held for 21 days, then fell through to the cooldown rule and was re-sent, every 21 days, forever. My episode fix had only moved the period from 14 days to 21 | After expiry, silence counts as a soft "no": quiet until 90 days after the first signal, unless strength rises |
| The warehouse replays a partition (duplicate rows) | `event_id` was in the model and never used. A duplicated month doubled consumption and fired the value family on its own | Rows are deduplicated on `event_id` in feature computation |
| The day's partition is one day late | `lag <= 1` passed, so the first late day was decided on a window missing its last day. In the replay, every late-data HOLD was on the second late day, and two signals went out on the first | Any missing partition holds |

The review also caught a flaw in the evaluation. **The simulated sellers knew the future.** They only dismissed accounts that weren't going to convert, so in the replay a dismissal could never cost a real conversion. Sellers are now outcome-blind, and per-signal precision went from 0.214 to 0.198. The miss audit also had a misleading label. "No behavior change" really meant "no signal fired", and it included accounts whose behavior had moved but stayed below threshold. It's now split.

Four bugs across the three rounds share one pattern: a field or concept was in the model, and nothing enforced it (`owner_active`, `largest_event_share_30d`, `event_id`, episode expiry). A test that only checks the easy side of a boundary passes in that situation. Each of the new tests checks the other side (day 22 and day 70, lag 1, duplicates).

## What I'd watch for next

- **Velocity is late.** In the replay, many *org adopter* accounts converted before the 30-vs-30 velocity crossed 2x. Median lead time is 16 days. A shorter window (14 vs 14) or a slope over weekly buckets might fire earlier, at the cost of more noise. That should be backtested, not assumed.
- **Capacity doesn't bind in the replay** (0 account-days held in 1.5). With 12 owners and 400 accounts that is expected. In production, capacity needs a real number from Sales before launch.
- **The coverage audit is circular here.** It catches 7 of 7 missed large steady accounts because the synthetic large steady accounts are, by construction, the top decile. On real data its hit rate is unknown.
- **Seasonality.** A 30-vs-30 velocity reads a quarter-end, a budget cycle or an annual event as growth. The synthetic world has no seasonality, so nothing here tests it. Real data would need year-over-year or same-period baselines.
- **Merge runs before capacity.** If capacity holds the winning product account, its sibling on the same CRM account still says "merged". Nothing is delivered either way, so the seller isn't affected, but the reason shown is less precise than it should be.
