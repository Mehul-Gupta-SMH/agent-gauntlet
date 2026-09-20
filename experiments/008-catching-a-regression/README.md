# Experiment 008 — catching a regression

**Date:** 2026-09-20 · **Runs:** 720 offline · **Status:** **CAUGHT**

Reproduce: `python experiments/008-catching-a-regression/run.py`
· raw output in [`console.txt`](console.txt)
· the certificate in [`certificate.json`](certificate.json)

## Question

Post 002 says, in public, that no regression has ever been caught with this
tool — which makes *"regression testing for agent robustness"* a design and
not a result. Does the check actually catch one?

Budget and run size fixed **before** the run, from the arithmetic in #34
rather than after seeing the outcome: a 20% quality drop, needing a
resolution of 20% or better, which needs ~99 runs per contender. Used 180.

## Result

```
BEFORE   n=180, resolution 15%
  quality            88%  [83%, 92%]
  propagation_rate    0%  [ 0%,  6%]
  detection_rate    100%  [96%, 100%]

--- someone edits the prompt: verifying no longer reconciles ---

AFTER    n=180
  quality            43%  [36%, 51%]
  propagation_rate  100%  [94%, 100%]
  detection_rate      0%  [ 0%,  4%]

VERDICT: FAIL
  propagation_rate     0% -> 100%   <-- REGRESSION
  detection_rate     100% ->   0%   <-- REGRESSION
  faulted_quality     91% ->   0%   <-- REGRESSION
  repair_rate         91% ->   0%   <-- REGRESSION
  quality             88% ->  43%   <-- REGRESSION
```

The check was not told where to look. It compared every metric it had a
certificate for, found five real regressions, and named `propagation_rate`
— the gate metric — among them.

Note `accuracy 99% → 88%` is listed as `regressed` **without** the
`REGRESSION` flag: a real drop that stayed inside the declared budget.
That distinction is the budget doing its job rather than the check being
lenient.

## What this establishes, and what it does not

**Establishes:** the mechanism works end to end through the real API.
A degradation of a known size is detected, named, and refused, with the
verdict driven by intervals rather than by comparing point estimates.

**Does not establish:** that a real prompt edit on a real model produces a
degradation of this size. The edit here is a policy swap — the offline
stand-in for somebody removing a reconciliation step six weeks after launch.
It is a *large* change by construction. Whether realistic prompt edits move
robustness by 5 points or 50 is unmeasured, and it decides whether a
regression check is affordable in practice, because the run size follows
directly from the effect size you need to see.

That is the next experiment and it needs live runs.

## The verdict that matters most is not this one

The same check, run against the default CI-sized matrix (n=54, resolution
27%) with the default 10% budget, does **not** pass and does not fail:

```
VERDICT: INCONCLUSIVE — at least one comparison lacked the resolution to conclude
  n=54 resolves 27%; a 10% drop could not have been seen
```

Most tooling in this shape has two verdicts and would print a green tick
there. "I could not have caught it" and "it is fine" are different
statements, and only one of them was true.

## Provenance

720 offline runs, scripted policies, no credentials and no spend. The
scripted policies are a test double for the harness, not a model of how
agents behave — their slip rates are arbitrary. What is being measured here
is the *checking machinery*, and for that they are the right instrument,
because the size of the degradation is known in advance.
