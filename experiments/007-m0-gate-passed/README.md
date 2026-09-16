# Experiment 007 — The M0 gate passes

**Date:** 2026-09-16 · **Runs:** 324 live · **Duration:** 36m30s · **Status:** **GATE: PASS**

[Actions run 35149486157](https://github.com/Mehul-Gupta-SMH/agent-gauntlet/actions/runs/35149486157)
· raw board in [`console.txt`](console.txt)

## Question

**Does a ranking of agent configurations survive a change of random seed?**
(#1) The question the project exists to answer, unanswered since the first
commit.

Bar pre-registered on 2026-09-15, before any live matrix: median Kendall's
tau ≥ 0.7, top-1 stability ≥ 0.6, seeds ≥ 3. Task fingerprint
`d11a0b2a6801ee74`.

## Verdict

```
variants=9  seeds=3  pairs=3  undefined tau=0
median tau      = 0.845
top-1 stability = 100%

GATE: PASS -- the ranking held across seeds at the bar set in advance.
```

**And it is not vacuous, which is the part that matters.** Experiment 003
"passed" tau at 1.0 and that number meant nothing: every variant tied, so no
ranking could be unstable. Here tau is 0.845 — the ranking *moves* between
seeds, genuinely — while every one of the three seed pairs still picks the
same winner. Discrimination and stability at once, which is the combination
the gate was designed to require and the previous fixture could not produce.

`undefined tau = 0`: no seed pair collapsed into a tie.

## The instrument check passes on the real board

```
cheap naive records-partial   0%  0.51   [sentinel]     <- last, by a wide margin
smart verifying records       50%  0.86                 <- next worst
```

The sentinel ranks last, 35 accuracy points below the worst real variant.
Experiment 003 failed here — its prompt-degraded sentinel placed 6th of 9 —
and per #29 that made its gate verdict unreadable. This board earns the right
to be read.

## Result — repair separates what detection could not

The column added days ago, immediately doing the job it was built for:

| variant | det | **rep** | reading |
|---|---|---|---|
| smart · verifying · records+summary | 100% | **100%** | notices and fixes |
| cheap · verifying · records+summary | 100% | **22%** | notices, rarely fixes |
| cheap · naive · records+summary | 100% | **0%** | notices every time, fixes never |
| smart · naive · records+summary | 100% | **0%** | same |

**Under the old four-outcome taxonomy all four of these read "detection
100%" and looked equivalent.** Three of them ship the corrupted total on
every faulted run. Detecting and repairing are different capabilities, and
until this board they were the same number.

## Result — the model axis is alive, and the attribution table understates it

Experiment 003 measured a model spread of **0%**. Here:

```
prompt   spread = 15%   naive 50%  -> verifying 65%
toolset  spread = 15%   records 50% -> records+summary 65%
model    spread = 10%   cheap 53%  -> smart 62%
```

But hold prompt and toolset fixed at `verifying · records+summary` and the
model axis moves repair from **22% to 100%** — a 78-point gap on the
capability that decides whether a falsehood reaches the user.

The one-at-a-time effect reports 10 points. That is not a small discrepancy;
it is the interaction problem in #22 demonstrated on live data. The caveat
that rides along with `factor_effects` is not boilerplate here — the number
beside `model` is wrong in a way that matters, and the board says so.

## Result — only one configuration survived the gate

Eight of nine variants propagated an injected falsehood on at least one
faulted run and were gated. The winner won a field of one — hence
`held-out rank 1 of 1`.

That is a real finding rather than a defect in the search: on this task, a
verifying prompt *and* a reachable cross-check *and* the stronger model are
all necessary to never ship a lie. Drop any one and propagation appears.

It does mean the held-out validation was undemanding this time: with a single
eligible candidate there was nothing for it to be wrong about. `optimism
+0.000` should be read as "not tested" rather than "no winner's curse."

## Finding — the run recorded no cost, and said so honestly

```
$/run: n/a for every variant
no run reported a price (offline, or the roll-up carried none)
```

This was a **live** matrix. It spent real money and recorded none of it.

`live_executor` had the `Trace` in hand — token counts, `cost_usd`,
durations, all verified complete back in [experiment 002](../002-live-path-validation/)
— read the final text off it and dropped the rest. `run_matrix` never set
`RunRecord.rollup`. Every cost number from 324 live runs is gone and cannot
be backfilled.

The honest-rendering rule written for #14 is what surfaced it. Because
`cost_complete` was false, the board printed `n/a` instead of `$0.0000`. Had
it printed zeros, the accuracy/cost frontier would have treated a paid matrix
as free and nobody would have looked twice.

Sixth instance of this project's recurring shape — **the output looked fine
and the number behind it was missing** — and the first one caught by a
guard written specifically to catch it.

Fixed in the same commit as this writeup: the executor returns its roll-up
alongside its answer, `run_matrix` records it, and all three probe call sites
unpack it. Tests pin the contract, including one asserting every probe call
site unpacks — because the fix would otherwise have broken the live probe on
its next run.

## What this establishes, and what it does not

**Establishes.** On this task, at k=2 repeats and 3 seeds, a leaderboard of
agent configurations is stable under a seed change, on a board whose
instrument check passes. The project is not an expensive random number
generator. M0's go/no-go is green, and the milestones behind it are unblocked.

**Does not establish.**

- **One task.** The held-out split partitions replications, not scenarios.
  This says nothing about whether the winner generalises past these three
  inventory scenarios (#20, still half open).
- **One framework.** `langgraph` only. The framework axis (#9) is untested.
- **k=2.** The gate says a ranking is stable at two repeats per cell; it does
  not say two is enough in general, which is the "at what k" half of #1.
- **A field of one.** Domination had one candidate to choose from, so the
  winner-selection machinery was barely exercised.
- **No cost data**, per the finding above, so the cost-aware half of the
  leaderboard is unvalidated on live data.
