# 015 — What the flat table misses, and in which direction

**Status:** #47's step 1, measured
**Cost:** $0.00 — computed from five probes already paid for
**Issues:** #47, #14

## The question #47 asked

> **Measure the bias before modelling it.** One live matrix reports
> `cost_usd` from the flat table; the provider's own reported cost for the
> same calls is the ground truth. The ratio between them is the caching
> discount actually obtained, per config. That is a number, not an
> estimate, and it needs no new pricing model — just recording both.

Both have been recorded for a while. Nobody divided them.

## The ratio

Five live `claude`-target probes. `cost_usd` is
`ResultMessage.total_cost_usd` — the SDK's own figure, the ground truth.
`flat $` is what `commonadk.runners.pricing` computes from the **same run's**
recorded token counts at the pinned rates ($1/$5 per MTok for haiku-4-5,
$3/$15 for sonnet-5).

| run | level | in | out | flat $ | SDK $ | ratio |
|---|---|---|---|---|---|---|
| 38039699257 | cheap | 1206 | 1225 | 0.007331 | 0.016763 | **2.29×** |
| 38040111747 | smart | 1186 | 886 | 0.016848 | 0.037212 | **2.21×** |
| 38041237112 | cheap | 1206 | 1657 | 0.009491 | 0.020773 | **2.19×** |
| 38041428236 | cheap | 1222 | 1972 | 0.011082 | 0.024785 | **2.24×** |
| 38041706579 | cheap | 1262 | 2116 | 0.011842 | 0.021650 | **1.83×** |

**The flat table underprices by roughly a factor of two, consistently,
across both model levels.**

## Which is the opposite direction from the premise

#47 reasoned that prompt caching makes the *real* cost **lower** than a flat
table, so the board overcharges exactly the configs that would be cheapest
in production. The measured gap goes the other way: the provider's own
figure is about **double** the table's.

So modelling a caching *discount* on top of this table would not correct the
number. It would make it worse.

## What the gap is not yet attributed to

This is a ratio, not an explanation, and the data cannot yet separate:

- **Token categories the counts omit.** Cache *writes* bill at 1.25× input
  and cache *reads* at 0.1×; if `prompt_tokens` excludes those categories,
  the table is pricing a fraction of the input.
- **Calls the counts omit.** The rollup carries a `count`, and the recorder
  was not storing it. It does now (`llm_calls`), so the next rows give a
  per-call rate.
- **Input the counts omit.** A large system prompt or tool-definition block
  that the adapter does not attribute to `prompt_tokens` would do this
  exactly.

Naming a cause here would be the thing #47 warned against: a figure derived
from assumptions about someone else's deployment.

## What it does settle

**#47's option 2, and on firmer ground than instinct.** The issue's author
wrote "my instinct is (2) until (1) produces a number." (1) has now produced
a number, and it says the modelled correction would point the wrong way.
Disclose; do not model.

**And it makes a cross-target cost comparison unsound, not merely rough.**
Only the `claude` target has ground truth — it is the one target commonadk
prices from the SDK. Every other target's `cost_usd` *is* the flat table, so
its ratio is 1.00 by construction rather than by measurement. A board that
ranks a `claude` config against a `langgraph` config on dollars is comparing
a measured cost against a modelled one, and the measurement says the model
is about half.

`_price_authority` now returns `pricing.MIXED` for that case and the board
says so. It used to return `UNKNOWN`, whose message reads "this target is
not in `pricing.AUTHORITY`" — a cause that was not the cause. Two different
situations, one message, naming the wrong one. That is the third instance of
that exact shape found today; the other two were `undetected_degraded`
asserting "no falsehood propagated" (experiment 014) and the probe
workflow's `case 2)` blaming a missing credential for four different exits.

## The defect found while setting this up

The cost and token figures in every row of `experiments/live/probe.jsonl`
are the **clean half's**, while the same row's `outcome`, `faulted_total` and
`propagated` are the **faulted half's**.

A probe makes three agent runs — clean, faulted, sentinel — and printed a
rollup for the first only; the other two had their rollups assigned to `_`
and dropped. So:

- a reader computing cost-per-detection from a row divides one half's
  dollars by another half's result, and
- the record understates what a probe actually costs by roughly **3×**.

Experiment 014's "$0.054 in live calls" is two clean halves, not two probes.
The real figure is unrecorded for those runs and cannot be recovered — the
consoles are in artifacts on a host this environment cannot reach (#46).

Fixed: each half prints a labelled rollup and the recorder stores
`faulted_*` and `sentinel_*` alongside. The bare names still mean the clean
half, so no already-committed row starts saying something it did not say.

## Caveats on the ratio itself

n=5, one target, one fixture family, clean halves only. The direction is
consistent across all five and across both model levels, which is what makes
it worth acting on; the magnitude is five observations and should not be
quoted as a constant.
