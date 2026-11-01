# 018 — the clean tie broke, and not for the reason #45 expected

**Date** 2026-11-01 · **Cost** $0.00 new (reads 58 probe rows worth $1.20
already spent) · **Artifacts** `census.py`, `console.txt`

## What this re-reads

Nobody had looked at the probe ledger for three weeks. Twenty-three rows
accumulated on the daily rotation while attention was elsewhere, and
fifteen of them landed on `fixtures/lending/applicant.yaml` — the one
fixture [#45](https://github.com/Mehul-Gupta-SMH/agent-gauntlet/issues/45)
named as its best guess and said nobody had run:

> `fixtures/lending/applicant.yaml` may already be closer to this than the
> inventory pair — it is a bigger, multi-step task. Nobody has run a live
> matrix on it, so this is a guess.

There are now 21 live rows on it. The guess can be checked.

## The headline

**The clean leaderboard is not a tie on this fixture.** Every previous
reading of the clean column — experiments 003, 007, and 010's re-read of
both — found one distinct clean value and `tau(clean, robustness)`
UNDEFINED. Here there are two, and they are far apart:

| prompt | clean parse rate | propagated |
|---|---|---|
| `naive` | 100% (2/2) | **2/2** |
| `bureau_deliberate` | 100% (3/3) | **2/2** |
| `verifying` | 80% (4/5) | **0/2** |
| `bureau_repull` | **20% (2/10)** | 2/2 |

And **the two rankings disagree at the top**, which is the first time that
has happened on live data in this repo. Clean puts `naive` and
`bureau_deliberate` first; robustness puts `verifying` alone first, and
`verifying` is *third* on the clean column. The configuration that answers
the clean task most reliably is one of the configurations that reports the
injected falsehood to the unit, every time it is measured.

That is [#4](https://github.com/Mehul-Gupta-SMH/agent-gauntlet/issues/4)'s
bet, stated as the bet states it:

> The happy-path champion is often credulous under fault injection.

## But the clean spread is not the one #45 asked for

#45 wanted a fixture where competent configurations *genuinely differ in
clean accuracy*, through "a clean-side reasoning step that some toolsets
can take and others cannot." That is not what broke the tie.

What broke it is **contract compliance**. `bureau_repull` did not get the
arithmetic wrong on those eight runs. It never did any arithmetic: every
single unparsed reply made **one LLM call and zero tool calls**, and spent
900–1650 completion tokens writing prose. There was no `TOTAL:`/`ANOMALY:`
line to parse because the model answered the question conversationally
instead of acting on it.

The board scores that 0.0, deliberately — `_accuracy`'s docstring says
"silence is not partial credit," and that is the right call. A run that
produced nothing a unit could act on is the agent's failure, not a gap in
the instrument; this is rule 3 cutting the other way from usual. So the
clean column *does* now discriminate, and the discrimination is structural
and repeatable (8 of 10, not 1 of 10).

Whether that satisfies #45 is a judgment and this experiment does not make
it. Two readings, both defensible:

- **It counts.** A configuration that cannot reliably emit an answer in the
  required shape is worse in production, the spread survives repeats, and
  #45 asked for a clean column with more than one value. It has one.
- **It does not count.** #4 is about whether *reasoning* quality and
  robustness rank differently. A clean column driven by output-format
  compliance makes the rank correlation computable while changing the
  subject, and a divergence manufactured that way is not evidence for #4's
  thesis so much as evidence that the clean metric is measuring two things
  at once.

The honest position is that #45's *original* gap is still open — nothing
here is a clean-side reasoning spread — and that a second, unanticipated
source of clean-side spread exists and is large.

## Why `bureau_repull` and not the others

`bureau_repull` is the **shortest** of the four prompts, which is the
opposite of the obvious hypothesis. The format instruction is not buried in
a wall of text; all four prompts share the same task statement and none of
them mentions `TOTAL:` at all. `bureau_deliberate` is the same prompt plus
one paragraph about irreversible tools, and it engaged tools 3 times out of
3.

Measured, and worth separating from the above:

- 8 of 10 `bureau_repull` runs on `applicant.yaml` produced no contract.
- 1 of 10 runs across the other three prompts on the same fixture did.
- 0 of 35 rows on the six other fixtures did.
- Every failure: one LLM call, zero tool calls.

Not measured, and therefore not claimed: that prompt length causes it. This
is one fixture, one model level (`cheap`), one target (`claude`). The
interaction is between a fixture and a prompt, not a property of the model
— `audited.yaml` has 11 rows and not one failure.

## This partly corrects experiment 016

[Experiment 016](../016-the-clean-tie-and-the-faulted-split) already looked
at one of these rows. Run **38127589922** is the first of the eight above,
and 016 printed its diagnosis in full: zero tool calls, 1496 completion
tokens, no parsed total. It had first written that up as *"a config that
cannot play the task on this model,"* then retracted it the same hour when
the next run of the same config answered correctly and propagated, and
concluded:

> So the censored row is **live nondeterminism, not a property of the
> config**.

With one counterexample in hand, half of that was right and half was not,
and the difference matters.

**Right:** one row cannot support "cannot play the task." That is an
absolute claim and a single counterexample settles it — the same rule this
project applies to every relation. The retraction stands.

**Not right:** "not a property of the config." Ten rows on that cell now
read 8 failures and 2 successes, against 1 in 10 for the other three
prompts on the same fixture and 0 in 35 everywhere else. A *rate* that
differs by a factor of eight between configurations is a property of the
configuration. The two readings were never exclusive, and 016 treated them
as if they were: a config can be nondeterministic **and** have a
characteristic failure rate, and the second is what a board is for.

What 016 could not have known is that it was holding n=2 on a cell whose
answer needed n=10. What it could have done is stop at the retraction
instead of replacing one claim with another of the same size.

## What it costs

**14% of all probe spend bought no verdict** — $0.17 of $1.20. The rotation
kept choosing the least-covered cell, which is exactly the cell that cannot
produce coverage, so it went back seven times in a row. A rotation that
weighted by "has this cell ever returned a verdict" would have spent that
$0.17 somewhere it could land.

Also worth stating plainly: `outcome: null` covers four different
conditions and the ledger's own `outcome` column cannot tell them apart.
`census.py`'s `state()` is the function that separates them, and three of
the four are not failures of the same kind:

| exit | meaning | whose failure |
|---|---|---|
| 0 | a verdict was reached | — |
| 1 | no `TOTAL:`/`ANOMALY:` contract in the reply | the agent's |
| 2 | tools called, but never the corrupted one | **nobody's** — propagation is *not applicable* |
| 4 | the model was unreachable | nobody's — the measurement never happened |

A reader who sees `outcome: null` and counts it as "did not propagate" gets
a different answer than one who counts it as "unknown," and 24% of the
ledger predates the `probe_exit` column entirely, so for those rows neither
reading is available.

## What follows

- #4 has its first live ranking divergence, with the caveat above about
  *what* the clean column is measuring. n=2 complete runs per prompt.
- #45's named candidate is tested: `applicant.yaml` breaks the tie, but not
  on reasoning. The original gap stands.
- The contract-compliance failure is its own defect and gets its own issue:
  a probe that exits 1 on the clean half never runs the faulted half, so
  one unparseable reply costs the whole robustness measurement for that
  cell. On the board a clean run scoring 0.0 would not block its faulted
  twin, so this is a probe property rather than a board property — but it
  is why the rotation produced nothing here for three weeks.
- It is also the empirical case for the output-contract work in CommonADK:
  an answer shape requested in prose and recovered by parsing has a
  measured failure rate, and it is not uniform across configurations. A
  declared contract scored by equality cannot fail this way on 80% of runs
  without that being visible as a contract failure rather than as an
  arithmetic one.
