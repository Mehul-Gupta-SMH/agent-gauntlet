# Experiment 010 — does robustness actually rank differently from quality?

**Date:** 2026-09-23 · **Cost:** $0.00 (re-reads 648 live runs already paid for) · **Status:** **BET HOLDS, IN A STRONGER FORM THAN #4 CLAIMED**

Reproduce: `python experiments/010-robustness-vs-quality/run.py`
· raw output in [`console.txt`](console.txt)

## Question

[#4](https://github.com/Mehul-Gupta-SMH/agent-gauntlet/issues/4) is the bet
the chaos layer rests on:

> The happy-path champion is often credulous under fault injection. […] If
> robustness and quality correlate near-perfectly, then the chaos layer is
> expensive confirmation of a ranking we already had.

#4 says the test is cheap because the counterfactual pairs already exist. It
is cheaper than that: both live matrices this project has ever run are
committed in this directory, so the live half of the answer costs nothing.
The numbers below are **parsed out of those captures at runtime**, not
retyped — this writeup cannot quietly disagree with the artifact it cites.

## Result, live: the correlation is not low, it is undefined

Experiments [003](../003-live-m0-gate/) and [007](../007-m0-gate-passed/).
Two different fixtures, 648 real runs on claude-haiku and claude-sonnet via
langgraph, 8 contenders each after the sentinel is excluded:

```
  experiment 007  (fixtures/inventory/audited.yaml)
  contender                                       clean  faulted
  smart verifying records+summary                  100%     100%
  cheap verifying records+summary                  100%      22%
  cheap naive records+summary                      100%       0%
  cheap naive records                              100%       0%
  smart naive records                              100%       0%
  cheap verifying records                          100%       0%
  smart naive records+summary                      100%       0%
  smart verifying records                          100%       0%

  distinct clean values      1
  tau(clean, robustness)     UNDEFINED
```

Experiment 003, on the other fixture, is identical in shape: eight
contenders, one distinct clean value, tau undefined.

**Every contender answered correctly every single time its tools told the
truth.** The clean leaderboard is one eight-way tie. There is no happy-path
order for the robustness order to agree or disagree with, and Kendall's tau
correctly refuses to return a number for it.

So the bet in #4 — *the happy-path champion is often credulous* — is
understated for these tasks. **There is no happy-path champion.** The
faulted half is the only axis that ranked anything at all, and it ranked the
same configuration first across all three seeds.

**Not an artefact of binarising, either.** `Score.accuracy` exists precisely
to break ties a binary metric cannot — but these tasks set `tolerance: 0`, so
a run that counts as correct was *exact*, and an exact run grades 1.000. 100%
clean correctness forces clean accuracy to 1.000 for all eight. The graded
tie-breaker is the same tie. (Every bit of the variation in the board's
pooled `acc` column — 1.00, 0.90, 0.89, 0.87 … — comes from the faulted half.)

The sub-claim at the bottom of #4 ("even under high correlation, the
propagation signal may still discriminate") is confirmed a fortiori: the
propagation column separates 1 from 1 from 6 where the clean column
separates nothing.

## Result, offline: negative correlation, and why you should not quote it

The same two fixtures against scripted policies at declared slip rates,
k=30. Here the clean half *does* spread, because the policies slip:

```
  fixtures/inventory/audited.yaml   k=30
  contender                        clean  faulted
  smart-naive-records              0.933    0.000
  smart-naive-records+summary      0.922    0.000
  smart-verifying-records          0.922    0.000
  smart-verifying-records+summary  0.867    0.911  <- survives the fault
  cheap-naive-records+summary      0.833    0.000
  cheap-verifying-records          0.756    0.000
  cheap-verifying-records+summary  0.756    0.722  <- survives the fault
  cheap-naive-records              0.700    0.000

  tau(clean, robustness)     -0.109      (task.yaml: -0.272)
```

Negative on both. The two configurations that survive a fault sit **4th and
7th of 8** on the clean board. The mechanism is legible rather than
mysterious: cross-checking costs a little clean accuracy, because every
extra step is another chance to slip, and buys everything under fault.

**This is not evidence about real agents and must not be quoted as if it
were.** The offline policies were written by hand with exactly this
structure in them; finding it again is a statement about the harness. What
it does establish is that the two metrics are not redundant *by
construction* — the machinery can represent and detect a robustness ranking
that contradicts the quality ranking. That is the precondition for the live
question being answerable at all, and it was not previously checked.

## What follows

#4 lists two branches. The live data takes the first one — *"robustness is a
first-class axis on the board, and probably the marketing lead"* — and the
argument is stronger than the branch anticipated: on these tasks robustness
is not a first-class axis alongside quality, it is the **only** axis
carrying information.

## The honest limit, and the fixture that would remove it

Two fixtures, both inventory arithmetic, both built so that a competent
configuration gets the clean answer right. A clean-side ceiling is a
property of the task, not a law of nature. Experiment 003's writeup already
identified a *faulted*-side ceiling and hardened the fixture against it; the
clean-side ceiling survived that hardening unnoticed, and this is the first
time anyone has looked at it.

So the correct claim is bounded: **for tasks whose clean version any
competent configuration solves, robustness is the only axis with
discriminating power** — which is most tasks that reach production, but that
last clause is an argument, not a measurement.

This project currently has **no fixture on which a live clean run
discriminates between competent configurations.** Until one exists, #4's
correlation can be bounded but not measured. Building one turns this
existence proof into a measurement, and it is the obvious next move.

## Also noticed

The live ledgers for experiments 003 and 007 were never committed — only
`console.txt`. Those were ~$10 of real runs, and this experiment could have
been a per-run reanalysis instead of a re-read of a printed table. It is
narrower than it needed to be because the raw records are gone.
