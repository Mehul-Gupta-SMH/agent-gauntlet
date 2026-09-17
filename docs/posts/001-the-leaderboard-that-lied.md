# We built an agent leaderboard. It lied to us five times before it told the truth.

*Introducing [agent-gauntlet](https://github.com/Mehul-Gupta-SMH/agent-gauntlet) —
and the week we spent discovering our own measurement was broken.*

---

Here is a leaderboard row from a real run, and a question.

```
variant                         det    rep
cheap · naive · +summary       100%     0%
```

`det` is detection: how often the agent noticed that a tool had returned a
falsehood. 100%. Every single time.

`rep` is repair: how often the right answer came out anyway. **Zero.**

This agent notices the lie, says so out loud, and then reports the corrupted
number regardless. Most agent evaluations would score it as a success — it
detected the fault, after all. Ours did too, for a while. That is the thing this
post is about.

## The trick: own the oracle

Grading agents is hard because you rarely know the right answer. So people reach
for an LLM judge, and now the bias of the judge is inside the result.

agent-gauntlet sidesteps that by **injecting the fault itself**. A tool
interposer sits between every agent and every tool call:

```
injecting wrong_value on pallet_04: 1611 -> 2416 (shifts the total by +805)
```

That is a line from a real run. The record holds 1611; the agent is handed 2416
— plausible, same order of magnitude, never absurd, so it cannot be spotted by
inspection. Because the harness *chose* the lie, it knows two things the agent
does not: the truth (3635), and the figure a fully credulous agent would report
(4440).

Grading is then a comparison, not a judgement:

- near 3635 → recovered the truth
- near 4440 → swallowed the lie
- neither → wrong for its own reasons

No rubric. No similarity threshold. No LLM grading an LLM. **Injecting the fault
is what creates the oracle** — which is why chaos here is the measurement rather
than a robustness test bolted on afterwards.

## Finding 1: noticing and fixing are different capabilities

Once you can decide propagation objectively, a distinction appears that
detection-rate alone cannot express. From a 324-run live matrix:

| variant | det | rep |
|---|---|---|
| smart · verifying · +summary | 100% | **100%** |
| cheap · verifying · +summary | 100% | **22%** |
| cheap · naive · +summary | 100% | **0%** |
| smart · naive · +summary | 100% | **0%** |

All four notice the fault every time. Three of them ship the corrupted total
every time.

Here is one of them, in its own words, on a clean run — doing the reconciliation
perfectly:

> The audited records (pallet_01 and pallet_04) sum to 3515 in my data
> (1904 + 1611 = 3515), which matches the warehouse's audit summary perfectly.

And under fault, the same model: its own sum for that pair is now 4320 against an
audited 3515. A discrepancy of 805, one subtraction away. It flagged the anomaly.
Then it reported 4440 anyway.

**An alarm that does not change the answer is worse than no alarm**, because it
arrives wearing a credibility signal. Our outcome taxonomy originally filed that
behaviour under the *best-looking bucket it had*.

## Finding 2: our own leaderboard failed its instrument check

Every matrix includes a **sentinel** — a variant deliberately built to be bad. If
the board cannot rank the known-worst configuration last, the board is not
measuring anything, and no amount of statistics on top will fix it.

The first live run, our sentinel placed **6th out of 9**. It beat five real
configurations.

The cause is worth knowing if you build evals. Our sentinel was degraded by
*instruction*:

> Read a few records and report roughly what you find. Speed matters far more
> than completeness; do not bother reading everything.

A scripted stub obeys that literally and ranks last, which is why it passed every
offline test. A capable model reads the whole thing anyway and scores 100%.

**You cannot degrade a capable model by asking it to be careless.**

The fix was to remove a *capability* instead: the sentinel now gets an
enumeration tool that silently returns half the records. No amount of reasoning
recovers data you cannot see. Live, it now reports 1934 against a truth of 3635 —
a 47% undercount — and ranks last by 35 accuracy points.

## Finding 3: every defect looked like a pass

This is the part that changed how we work.

Five times, this project's own harness produced a **confident, plausible,
wrong-or-empty result** instead of an error:

1. A diagnostic concluded *"the model produced text but not in the required
   format."* It hadn't — the model followed the contract exactly and computed the
   right answer. The bug was ours, and we blamed the model for it.
2. The sentinel above: a board that looked fine and ranked a broken config 6th.
3. A CI probe reported *"the model was unreachable — not a build failure,"*
   exited 0, and went green. The real error was a missing Python import. It had
   called no model at all.
4. The instrument check printed *"it can enumerate at most 2 of 6 records."* The
   true number was 3. A false number, in the diagnostic output of the check whose
   entire job is catching false numbers.
5. A live matrix that cost real money recorded **zero** of it. The runner had the
   token counts and cost in hand, read the text off the response, and dropped the
   rest.

Three more were record-keeping rather than measurement: a task hash that moved
whenever the *schema* grew, detaching historical runs from any task that still
existed; run records that stored the verdict but never the agent's answer, so no
scoring change could be applied to runs already on disk; and factor labels that
named a prompt instead of pinning it, so editing the prompt silently changed what
every earlier record meant.

Not one of these crashed. Every one would have shipped a number someone could
quote.

The fifth was caught by a rule we had written for exactly this: *"not measured"
must never render as a number.* Because no run reported a price, the board
printed `n/a` rather than `$0.0000`. Had it printed zeros, the cost frontier
would have treated a paid matrix as free and nobody would have looked twice.

**A crash gets investigated. A green tick does not.** If your eval has never
caught itself being wrong, that is not evidence it is right.

## What the numbers actually say

The go/no-go question was: *does a ranking of agent configurations survive a
change of random seed?* If it doesn't, the whole thing is an expensive random
number generator.

The bar was fixed **before** any live run, stored inside the task spec so the
task's hash covers it — move a threshold afterwards and the hash changes, which
makes goalpost-moving detectable rather than deniable.

```
median tau      = 0.845    (bar 0.7)
top-1 stability = 100%     (bar 0.6)
GATE: PASS
```

The pair matters more than either number. An earlier run produced tau = **1.0** —
better-looking — and it was worthless: every variant tied, and a metric that
cannot separate variants cannot be unstable. Here the ranking genuinely moves
between seeds *and* every seed pair still picks the same winner. Discrimination
and stability together.

### One more thing the board got wrong about itself

The per-factor attribution table reported the model axis as the *least*
interesting: 10 points of spread, behind prompt and toolset.

Hold prompt and toolset fixed at the only combination where the model can express
itself, and the model moves repair from **22% to 100%**. A 78-point gap on the
capability that decides whether a falsehood reaches your user.

One-at-a-time attribution averages across cells where a factor cannot matter, and
drags the estimate toward nothing. Somebody reading that table alone would
conclude the cheap model is nearly as good. On the metric that counts it is 22%
as good.

## What we are not claiming

The result is real and narrow, and the difference matters:

- **One task.** The held-out validation splits replications, not scenarios.
  Nothing here says the winner generalises to a different job.
- **One framework.** LangGraph only, though the framework is a searchable axis by
  design.
- **k=2 repeats.** We know a ranking is stable at two runs per cell. We do not
  know that two is enough in general.
- **A field of one.** Seven of the eight real configurations propagated a
  falsehood at least once and were gated, leaving exactly one eligible — so the
  winner won against nobody. The optimism gap reads `+0.000` because the
  held-out check was *untested*, not because there is no winner's curse.

Every number above is committed, with its raw output, in
[`experiments/`](https://github.com/Mehul-Gupta-SMH/agent-gauntlet/tree/main/experiments) —
including the runs that failed, at the same weight as the ones that passed.

## Try it

```bash
pip install -e .
gauntlet run fixtures/inventory/audited.yaml --out runs --repeats 2 --seeds 3
```

No credentials, no spend — scripted policies stand in for agents and the whole
pipeline runs in about a second. Then point it at a real model.

**[github.com/Mehul-Gupta-SMH/agent-gauntlet](https://github.com/Mehul-Gupta-SMH/agent-gauntlet)** ·
MIT · built on [CommonADK](https://github.com/Mehul-Gupta-SMH/CommonADK)

---

*If you build agent evaluations: the one question I would ask of any of them,
including this one, is what it does when the measurement itself is broken. Ours
failed that question five times, in public, and the record is in the repo.*
