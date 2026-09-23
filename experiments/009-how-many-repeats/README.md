# Experiment 009 — how many repeats does a stable board need?

**Date:** 2026-09-23 · **Runs:** 177,408 offline in 29s · **Status:** **MEASURED**

Reproduce: `python experiments/009-how-many-repeats/run.py`
· raw output in [`console.txt`](console.txt)
· the numbers in [`curve.json`](curve.json)

## Question

Issue #1 asks whether the leaderboard survives a seed change, **and at what
k**. The gate existed and experiment 007 passed it once, at `--repeats 3`,
a number nobody had measured. Every ranking claim the project makes rests
on that number, and it was assumed.

`k` here is always *repeats per cell*. The issue's other k — how far down
the ranking to look — is `m` throughout, the **depth**.

## Result

Eight seeds (28 pairs), eight contenders, the shipped slip rate:

```
    k   runs   med tau   top-1  decidable  verdict vs the pre-registered bar
    1     48     0.627     57%    28/28    fail
    2     96     0.683     57%    28/28    fail
    3    144     0.699    100%    15/28    fail
    4    192     0.717    100%    28/28    PASS
    5    240     0.741    100%    28/28    PASS
   ...
   50   2400     0.775    100%    28/28    PASS
```

**k=4 is where the board stops moving with the seed** — on this task, at
this noise level. `--repeats 3`, the setting every M0 run has used, sits one
short of it. Not because the winner disagreed at k=3: among the seed pairs
where a winner existed at all, every single one agreed. It sits short
because on 13 of the 28 pairs there *was* no winner, and a gate must not
report agreement it never observed.

Three things fall out, and only the first was the question.

### 1. tau is the wrong number to read

Median tau moves from 0.627 to 0.775 across a 50× budget increase, crossing
its bar somewhere it is impossible to point at. Top-1 stability goes 57% →
57% → 100% and stays. The decision — *is the winner the winner* — is
answered sharply; the whole-ranking correlation is a slow blur. The gate
already reads top-1 first. This is the measurement that says it should.

### 2. depth 2 settles before depth 1

```
    k  m=1         m=2         m=3         m=4
    1  16/28       21/21 *      0/0  *      0/0  *
    3  15/15 *     28/28        1/3  *      1/3  *
    4  28/28       28/28        1/6  *      2/6  *
   50  28/28       28/28        7/28         4/15 *
```

*Which two* configurations are the best two is settled at a budget that
cannot yet say which of the two is better. And depths 3 and 4 never settle,
at any k in the sweep — 7 of 28 pairs at k=50, with 2,400 runs behind every
cell.

### 3. that is not a sampling problem, and budget will not fix it

```
  rank  correct  gap to next  contender
     1   0.9062       0.1342  smart-verifying-records+summary
     2   0.7721       0.3079  cheap-verifying-records+summary
     3   0.4642       0.0013  smart-naive-records+summary
     4   0.4629       0.0025  smart-verifying-records
     5   0.4604       0.0633  smart-naive-records
```

Ranks 3, 4 and 5 are separated by four thousandths. They are **tied in
truth**. Their order is a coin flip at every budget, and 7/28 ≈ the 1-in-3
you would get from picking the third slot at random. A board that prints
them in order is reporting noise with a confident face — which is the thing
this project exists to refuse, appearing one row below where anyone looks.

So the honest answer to *"full ranking or top-k?"* is neither: report the
depth the data supports, and censor below it.

### The k that transfers is no k at all

```
  slip 0.22/0.08  (as-shipped)  bar first cleared at k=4
  slip 0.11/0.04  (half      )  bar first cleared at k=20
  slip 0.44/0.16  (double    )  bar first cleared at k=3
```

Quieter agents needed **five times more** repeats, not fewer. Halving the
slip rate also halves the gap between the two model levels — in a Bernoulli
policy the mean and the variance move together, and that confound cannot be
removed inside this model. Which is exactly the finding: k tracks the
*separation between adjacent configurations relative to the noise*, and
neither term is knowable before the run.

A k carried over from someone else's task is worth nothing. Sweep it on
yours.

## What this does not show

Scripted policies at declared slip rates (`offline.MODEL_SLIP`), one
fixture, one grid. This is a property of the **measuring machinery under
known noise**, not a claim about real agents: it cannot tell an operator
what k a live matrix on a real model needs, which is still unmeasured
(#33). What transfers is the method — sweep k, read top-1, stop where it
flattens, refuse the depth that never flattens.

The sentinel is excluded from the grid on purpose. It is built to lose, and
a variant that is reliably last inflates every rank correlation it appears
in.

## What changed because of it

- `analyze.stability` now drops undecided pairs from the top-1 denominator
  instead of counting a tied top as a disagreement, and reports how many it
  dropped. At k=3 that is the difference between "54%, unstable" and
  "100% of the 15 pairs that could answer" — different diagnoses, different
  fixes. It mirrors what `DetectionReport` already did for runs with no
  reachable evidence.
- The gate fails on any pair with no decidable winner, the same
  zero-tolerance rule it already applied to undefined tau, one depth up.
  This does not move experiment 007's verdict: it had three decidable pairs
  out of three.
