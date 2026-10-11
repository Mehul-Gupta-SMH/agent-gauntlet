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

## What the ratio actually measures — a correction

The paragraphs above were written before the per-half rollups landed, and
the first thing those rollups showed is that **the ratio does not measure a
pricing bias.** Its denominator is broken.

`prompt_tokens`, across ten live `claude` rows spanning four fixtures and
four tool sets, takes four distinct values:

| fixture | tool set | in | out |
|---|---|---|---|
| discontinued.yaml | records | 1206 | 1225 |
| instruction.yaml | records+annotations | 1206 | 1657 |
| audited.yaml | records+summary | 1206 | 1520 |
| memory.yaml | records+notes | 1222 | 1972 |
| discontinued.yaml (smart) | records | 1186 | 902 |

Three different task statements and three different tool sets reporting
**1206 input tokens each**. That is not an input count. It tracks the model
level — both `smart` rows read 1186 — and almost nothing else.

Within a single probe it is worse. The clean, faulted and sentinel halves
are three separate runs, and the sentinel is a *different variant with a
different tool set*; two probes report the identical 1206 for all three.

`llm_calls` says why. The rollup's `count` is the number of `LLMCall` events
in the trace, and for this target it is **1** — while the same run's
`tool_calls` is four or more. An agent cannot reach four tools in one model
turn. So the claude adapter surfaces one call's usage while
`ResultMessage.total_cost_usd` bills the whole multi-turn conversation, and
the "flat estimate" is one turn's tokens priced against a whole
conversation's cost.

**The ~2.2× is approximately the number of turns the token counts omit.** It
is not a caching discount, and it is not a bias in the rate table.

### The cause, upstream

`ModelUsage` — the typed dict the claude adapter reads — declares **four**
input-side counts:

```python
inputTokens: int
outputTokens: int
cacheReadInputTokens: int
cacheCreationInputTokens: int
```

`commonadk/runners/claude_agent.py` summed the first and last and discarded
the two in the middle. For a multi-turn agent run that is most of the input,
because every turn after the first reads the prefix from cache — so
`prompt_tokens` is the small, nearly constant *uncached* delta while
`total_cost_usd` bills all of it.

That accounts for every observation here: the near-constant 1206, the ~2.2×
gap, and the faulted half's lower ratio (its input is proportionally more
cache *reads*, billed at a tenth, so its cost sits closer to the uncached
estimate).

**Fixed upstream.** `LLMCall` now carries `cache_read_tokens` and
`cache_write_tokens`, the rollup sums them, and the rollup's `note` states
that `prompt_tokens` is the uncached portion whenever cache tokens are
present. They are kept separate from `prompt_tokens` rather than folded in,
because a read bills at a fraction of the input rate and a write at a
premium: one number cannot carry three rates, and folding them in would fix
the magnitude and break the pricing.

The regression test that should have caught it had both cache fields pinned
at `0` — where a dropped zero is indistinguishable from a carried one. It
now uses non-zero counts, verified failing without the fix.

`tools/probe_record.py` captures both fields **already**, before the release
that provides them, and records null until then. A recorder that learns
about a field when somebody notices it missing is how twelve probes got paid
for with nobody reading them (experiment 013) and how this very number came
to be priced as the whole input.

### And `usage_complete` cannot catch it

It reads `True` on every one of these rows. Its definition, from
`commonadk/runners/trace.py`:

```python
usage_complete = count == 0 or len(reported) == count
```

"Every LLM call I **saw** reported usage" — not "I saw every LLM call." The
flag is honest about what it checks and was being read as a guarantee it
never made. A completeness flag computed over the events an adapter chose to
emit cannot see the events it did not.

Which is the same shape as the rest of this day's findings: a field whose
name claims more than its definition supports, read as the claim.

The row now carries `tool_calls` beside `llm_calls`, plus `reported_count`,
`usage_complete` and `cost_complete` per half, so the contradiction is
readable off the record rather than reconstructed from the adapter's source.

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

**Read that section with the correction above in hand.** The *net* ratio
does point away from a caching discount. But it is dominated by the missing
turns, so it is not evidence about caching either way — and there is a hint
in the other direction underneath it: within a probe, the faulted half
(which runs second, on the same prefix) shows a consistently *lower* ratio
than the clean half at the same rates — 1.23×, 1.32×, 1.32× against 2.20×,
2.10×, 2.69×. That is the shape a cache read would make. One `smart` probe
shows no such gap (2.18× vs 2.19×), so it is three observations and a
counter-example, not a finding.

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
and dropped. So a reader computing cost-per-detection from a row divides one
half's dollars by another half's result.

The understatement is now **measured**, on the first four rows that carry
all three halves:

| run | clean | faulted | sentinel | probe total | recorded as | understated |
|---|---|---|---|---|---|---|
| 38043132742 | 0.015514 | 0.008353 | 0.013956 | 0.037823 | 0.015514 | **2.44×** |
| 38043369177 | 0.037364 | 0.036084 | 0.006169 | 0.079617 | 0.037364 | **2.13×** |
| 38043622964 | 0.021439 | 0.012867 | 0.015076 | 0.049382 | 0.021439 | **2.30×** |
| 38052168162 | 0.019185 | 0.012923 | 0.016569 | 0.048677 | 0.019185 | **2.54×** |

**2.13×–2.54×, not "roughly 3×"** as this write-up first said. Three runs is
not three times the cost: the clean half is the most expensive of the three
in every case, because it is the one that does the whole task from a cold
start.

Experiment 014's "$0.054 in live calls" is two clean halves, not two probes.
The real figure for those two runs is unrecorded and cannot be recovered —
their consoles are in artifacts on a host this environment cannot reach
(#46). On the ratios above it was closer to $0.12.

Fixed: each half prints a labelled rollup and the recorder stores
`faulted_*` and `sentinel_*` alongside. The bare names still mean the clean
half, so no already-committed row starts saying something it did not say.

## Caveats on the ratio itself

n=5, one target, one fixture family, clean halves only. The direction is
consistent across all five and across both model levels, which is what makes
it worth acting on; the magnitude is five observations and should not be
quoted as a constant.
