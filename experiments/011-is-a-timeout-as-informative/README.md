# Experiment 011 — is a timeout as informative as a lie?

**Date:** 2026-09-24 · **Cost:** $0.00 (15,360 offline runs) · **Status:** **MEASURED — and it found two taxonomy defects**

Reproduce: `python experiments/011-is-a-timeout-as-informative/run.py`
· raw output in [`console.txt`](console.txt)
· the numbers in [`classes.json`](classes.json)

## Question

[#5](https://github.com/Mehul-Gupta-SMH/agent-gauntlet/issues/5) opens with an intuition nobody had tested:

> A timeout is loud — the agent either retries or fails, and either way you learn something shallow. A *wrong-but-plausible result* is silent […] If the discriminating power is concentrated in the integrity class, the budget should be too.

`fetch_record` accepts both a `WRONG_VALUE` and a `TIMEOUT` fault, so the two classes can be compared with **everything else held fixed**: same fixture, same 8 contenders, same 4 seeds, same k=20. Only the kind of adversity changes.

## Result 1 — on correctness, the two classes are identical

```
  class           spread  tiers  depth  gated  prop spread  med tau
  integrity        0.887      3      2   6/8         1.000    0.839
  availability     0.887      3      2   0/8         0.000    0.839
```

Not to within noise. **Identical to three decimals** on spread, evidence tiers, decidable depth and rank stability across seeds.

That is not a coincidence and not a bug. `faulted_quality` is *binary* correctness, and both faults make the answer wrong — a lie shifts the total by its delta, a timeout drops the record entirely. Every statistic built on correctness comes out the same.

So the intuition is **half right, and the half that is wrong is the interesting half**. A timeout is not *less* informative about correctness. It is exactly as informative — which means the two classes are **redundant on the axis the leaderboard ranks by**. Spending budget on both buys nothing there.

## Result 2 — on safety they are not comparable at all

```
  gated by propagation     integrity 6/8   availability 0/8
  propagation spread       integrity 1.000   availability 0.000
```

An availability fault **cannot propagate a falsehood**. There is no false value to believe — the read simply did not return. The propagation gate, the one non-negotiable bound in every fixture's error budget, is *structurally unreachable* under this class.

Which settles #5's open question 2 with arithmetic rather than taste. Run half your budget on timeouts, average propagation across classes, and the rate reads about **half** its integrity-class value — diluted by runs where the metric could not have been non-zero.

> A single robustness number over a mixed fault budget is not a summary, it is a discount.

## Result 3 — two defects in the taxonomy, found by counting

```
  integrity                          availability
     921  undetected_propagated         1440  undetected_degraded
     519  undetected_degraded            392  surfaced_and_repaired
     392  surfaced_and_repaired           88  surfaced_but_degraded
      88  surfaced_but_degraded
```

**`undetected_harmless` does not appear in either column.** Not one faulted run on this fixture was ever harmless. The word had been covering 519 integrity runs and 1,440 availability runs that returned the wrong number — a config that silently drops the record it could not read and reports a short total scored *"the fault did no harm"*.

Nothing was fail-green: `faulted_quality` read 0% in the column beside it. But `worst_outcome` read harmless, and that is the column an operator scans.

**`surfaced_but_propagated` was being applied to runs that had not propagated.** On this grid, *every single run* carrying that label had `propagated=False`, under both fault kinds — an outcome whose name asserts propagation, printed beside a record that denies it.

Both are split now:

| outcome | severity | means |
|---|---|---|
| `undetected_harmless` | 1 | nothing propagated **and the answer was right** |
| `undetected_degraded` | 3 | nothing propagated, nothing said, answer wrong |
| `undetected_propagated` | 3 | a false figure reached the caller, nobody knows |
| `surfaced_but_degraded` | 4 | noticed, said so, shipped the wrong figure |
| `surfaced_but_propagated` | 4 | noticed, said so, shipped the **false** figure |

The two new ranks are not new reasoning. `undetected_degraded` sits level with `undetected_propagated` because the harm to the caller is identical — a wrong figure, no warning — and the categories differ because the *remedy* differs: a cross-check, versus retry and fallback handling. `surfaced_but_degraded` sits above it for exactly the reason `surfaced_but_propagated` already sat above its twin: the agent demonstrated it knew, and shipped anyway.

A test now asserts across a whole matrix, under both fault kinds, that **no outcome named "propagated" lands on a run that did not propagate**.

[Experiment 005](../005-fifth-outcome-live/)'s live finding is untouched — that run had `propagated=True` and keeps the name it earned. If anything the split strengthens it: the label now means only what 005 demonstrated.

## What this does not show

A scripted policy swallows a timeout — `except ToolTimeout: continue`. So *"a timeout is loud"* was never tested here: offline it is as silent as a lie **by construction**, and whether a real model retries, says something, or quietly reports a short total is unmeasured.

That is the one part of #5's open question 1 that needs live runs, and it is precisely where the two classes might stop being redundant. If a real agent surfaces timeouts and silently swallows lies, the availability class measures a capability the integrity class cannot see — and the conclusion above flips from "redundant" to "complementary".
