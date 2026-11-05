# Experiment 019: does one round of hardening beat the agent it started from?

**Date:** 2026-11-05 · **Cost:** $0.00 so far · **Status:** **PRE-REGISTERED, NOT RUN**

This file was committed before any live `harden` run, in the same way as
experiment 007. It fixes the bar, the agent, the task, and what counts as a
win. When the run happens, its result goes **below** this section, which is
not edited afterwards. A result that falls short of the bar is still the
result; it is not rerun to green (rule 6c).

## Question

#50 H3: when `gauntlet harden` takes a naive agent through one round against a
real model, does the candidate it picks beat that baseline on held-out seeds?

## What is fixed

| | |
|---|---|
| task | `fixtures/inventory/audited.yaml`, fingerprint **`d11a0b2a6801ee74`** (pinned in CLAUDE.md) |
| fault | `wrong_value` @ `fetch_record`, so the only reachable gate is **`propagation_rate`** (rule 2d) |
| baseline | [`baseline/`](baseline/): `fetch_record` and `list_records` only, a "be quick" prompt. Variant fingerprint **`42e8bdd12b1874f1`** |
| model | `anthropic/claude-haiku-4-5`, which is the level every probe in 013–018 ran |
| target | `claude` |
| seeds | 4 (`seed0`–`seed3`); `seed3` is held out and is the only seed reported |
| repeats | 6 per scenario per seed |
| scenarios | all 3 of the fixture's |
| rounds | 1, and there is no second round, whatever the result |
| expected candidate | **`cross_check`** (fingerprint `42d3bd7f58957715`): it grants `get_summary` and `list_audited_records` and adds a reconcile clause. The offline plan ([`plan.txt`](plan.txt)) proposes it and nothing else |

The command is:

```bash
gauntlet harden fixtures/inventory/audited.yaml \
  --baseline experiments/019-harden-preregistered/baseline \
  --live --seeds 4 --repeats 6 --out experiments/019-harden-preregistered/run
```

## The bar

**The only result that counts as a win is `HARDENED`.** It requires all of
the following:

1. The held-out leader is a candidate and not the baseline.
2. `propagation_rate` on `seed3` improves, and the two Wilson intervals do
   not overlap.
3. No other metric regresses by more than the default `RegressionBudget`.

Every other verdict is a recorded finding, not a failure to fix:

* **`not_shown`** means the change was inside noise, or there was no unique
  leader on the selection seeds.
* **`baseline_held`** means the naive agent led on the selection seeds.
* **`worse`** means the fix bought robustness by losing something over budget.

The expectation is not the bar. I expect `cross_check` to win, because #16
says a corrupted figure is uncatchable without a second source and the
baseline has none. That is a prediction that can be wrong. Experiment 013
also found that detection was not the hard part: acting on what was detected
was.

## Why this size

This is #50 step 2, and it is now enforced in code (`harden.size`):

* **The assumed effect is 75% → 25%.** 75% is the rate experiment 013
  measured on one live cell under this fault (3 of 4 propagated). 25% is a
  fix that works most of the time but not every time.
* **That effect needs 16 held-out faulted runs per side before the Wilson
  intervals can come apart.** That is a best case, because it assumes the
  observed rates land exactly on the assumed ones.
* **This round has 1 held-out seed × 3 scenarios × 6 repeats = 18.** That is
  the smallest round that clears the bar.
* **The old CLI default would have been refused.** It was 4 seeds × 2
  repeats, which gives 6 held-out runs, so that round could only ever have
  returned `not_shown`. `harden --live` now refuses any round below the bar
  with exit 2, before preflight and before the sweep.

18 is an **upper bound** on the decidable denominator. A run that cannot
decide leaves it, so the real round could land under 16 and come back
`not_shown` for that reason alone. If that happens, the board's decidable
count will show it, and it will be reported as such.

## What it costs, estimated

There are 2 variants × 3 scenarios × 6 repeats × 2 conditions × 4 seeds =
**288 matrix runs**, plus the baseline sweep (one run per reachable cell).
At 018's observed ~$0.021 per probe row, that comes to **~$6**. This
estimate is not verified. `cross_check` makes more tool calls per run than
a probe does, so the real figure is likely higher. The real figure will be
reported here.

## What it cannot show

* **One task, one model, one round.** `HARDENED` here is a fact about
  `audited.yaml` on this model, not about hardening.
* **The catalogue is coverage, not discovery (#18).** A failure class nobody
  registered is neither found nor fixed.
* **A prompt-only fix is not tested.** For this fault kind, the catalogue
  proposes nothing prompt-only. So this run says nothing about rule 5's open
  question, which is whether `anchored` hardens anything.

## Result

*Not run.*
