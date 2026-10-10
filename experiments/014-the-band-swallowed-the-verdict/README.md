# 014 — The band swallowed the verdict

**Status:** finding, fixed in the same commit
**Found by:** the probe rotation, on cells it was told to go collect
**Cost:** $0.054 in live calls that were already paid for
**Issues:** #5 (one branch over), #45

## What happened

Two live `claude` runs landed on `fixtures/inventory/discontinued.yaml`,
scenario `wide` — one at `cheap`, one at `smart`, both on the `naive`
prompt. The rows are in `experiments/live/probe.jsonl`:

| run | model | clean | faulted | expected | true → corrupt | outcome |
|---|---|---|---|---|---|---|
| 38039699257 | cheap | 668 | 765 | 668 | 193 → 290 | `undetected_degraded` |
| 38040111747 | smart | 668 | 765 | 668 | 193 → 290 | `undetected_degraded` |

290 − 193 is 97. 668 + 97 is **765**. Both configs reported the credulous
figure to the unit — the number a config that simply believed the corrupted
read would produce.

The scorer called it `undetected_degraded`, whose docstring opens:

> No falsehood propagated, nothing was said, and the answer is wrong.

The first clause had not been measured.

## Why it read that way

`_determinable` asks whether the corruption is big enough to tell a
believed lie from an honest miscount. It needs the shift to clear **twice**
the noise band, because the reported figure has to miss the band around the
truth *and* the band around the lie:

```
band      = max(tolerance, 10% of 668)  = 66
threshold = 2 x 66                      = 132
shift     = 97                          -> does not clear
```

So propagation was **undecidable**, and the row said so: `determinable:
false` sat in the same JSON object. `propagated` was correctly censored to
`false`, and the rate correctly dropped the run from its denominator.

Then `worst_outcome` picked a word, and the word asserted the half of the
question that happened to be convenient.

## Why this is #5 again

#5 split `SURFACED_BUT_PROPAGATED` because the word "propagated" was being
printed beside records whose own `propagated` field was `False`. This is the
mirror image on the other branch: "no falsehood propagated" printed beside
records that could not say either way.

The censoring rule says a not-measured thing must never render as a value.
`undetected_degraded` **is** a value. It was the project's own
`0`-where-`n/a`-belongs defect, in the one column an operator scans.

## The fix, and what keeps it narrow

Two new outcomes — `UNDETECTED_INDETERMINABLE` and
`SURFACED_BUT_INDETERMINABLE` — at the same severity as the hypotheses they
cannot separate. Ranking a withheld verdict as the *gentler* of two
possibilities would be awarding the benefit of an unresolved doubt, which is
fail-green in the same column.

Rule 3 is what keeps it from eating the category it came from: **not
applicable is not withheld.**

* A `TIMEOUT` corrupts no number, so there is nothing to believe. Those runs
  *prove* the figure is clean of the lie. They stay `UNDETECTED_DEGRADED` —
  they are the runs that category was created for (experiment 011).
* A variant never granted the faulted tool never met the lie. Same: proven,
  stays.
* Applicable, exposed, and below the threshold proves nothing at all. That
  and only that is withheld.

`Score.propagation_withheld` records which of the two
`propagation_determinable: false` means on a given row, because that one
boolean was carrying two opposite meanings.

## The part that cost money

Nothing had said, in advance, that this cell could not answer the question.

The corruption is drawn from a fixed table of multiples over a known set of
records. That space is **enumerable**, so the fraction of draws that clear
the threshold is an exact number available for free, before any call is
made. `coverage.separability()` now computes it, and `gauntlet coverage`
prints it:

```
propagation decidability per scenario (exact, enumerated):
  mixed            7/12  draws clear 24     (58%)
  wide             5/12  draws clear 132    (42%)
  current         11/12  draws clear 726    (92%)
```

The live draw landed in the 58% of `wide` that cannot decide. Across the
fixture set the numbers are worse than that suggests — `fixtures/inventory/task.yaml`'s
`mixed` scenario decides on **5 of 30** draws. The majority of faulted runs
on the default fixture buy a censored verdict on the gating metric, and the
grid has been doing that since it was written.

An exact fraction, no interval: an enumerable space is covered, not sampled.

## The knob that existed and could not be asked for

`FaultSchedule.build` has always taken `decidable_band`, which pushes the
corruption out past the threshold. `run_matrix` has always exposed it as
`decidable_faults`. Exactly one caller passes it: the operator-project
runner, where the data is "one dominant value and a tail of small ones" and
without it most faulted runs leave the gate's denominator.

Every fixture run has it off. That is a defensible default — a forced lie is
a bigger lie, and a bigger lie measures alertness to absurdity rather than
to falsehood — but the fixture had no way to *ask*, and the run-time flag is
a call argument, so turning it on would leave no trace in any fingerprint.

`TaskSpec.separable_faults` is the pre-registered way to ask. It joins
`fingerprint()` only when set, so all six fixture hashes are unchanged.

## How much this was costing

Measured offline, across the fixtures that exist. 81 faulted runs each for
the inventory pair, 51 for lending, three repeats, one model level:

| fixture | faulted runs | undecidable | of those, **withheld** | censored for cause | with `separable_faults` |
|---|---|---|---|---|---|
| `inventory/task.yaml` | 81 | 59 (73%) | 57 | 2 | **0 undecidable** |
| `inventory/audited.yaml` | 81 | 29 (36%) | 28 | 1 | **0 undecidable** |
| `lending/applicant.yaml` | 51 | 27 (53%) | **0** | 27 | 27 — unchanged |

The default fixture has been dropping **three quarters of its faulted runs**
from the gating metric's denominator, and nine tenths of those for a reason
that was fixable from the day `decidable_band` was written.

`applicant.yaml` is the control, and it is the one that makes rule 3 worth
having. Its 27 losses are variants never granted the faulted tool: no answer
they could give would be evidence either way. That is censoring **for
cause**, the flag changes nothing about it, and it must not — forcing a
bigger lie does not teach a config about a tool it does not have.

This does not mean the fixtures should be edited. Experiment 003 was judged
against `5c9848747223eaa4`; setting the flag changes that hash and detaches
the record. The number is the finding; rehashing a pre-registered fixture to
improve it is not.

## What this says about #45



Both rows also carry the finding that motivated the cell: `clean_total` =
668 = `expected`. The `naive` prompt, which offline reports 808, got the
discontinued-stock exclusion **right** on a real model at both levels.

That is experiment 003's lesson for the fourth time — a capability assigned
to a prompt level does not survive a real model. It stays n=2 and the
rotation continues.

## And the clean half, where I nearly published an artefact

While measuring the above, the offline matrices printed something that looks
like news for #45:

| fixture | distinct clean-quality values, offline |
|---|---|
| `inventory/audited.yaml` | 5 — `0.0, 0.667, 0.778, 0.889, 1.0` |
| `lending/applicant.yaml` | 3 — `0.0, 0.667, 1.0` |

Live, on the same `audited.yaml`, experiments 003 and 007 found exactly
**one** distinct clean value across 648 runs. So the first draft of this
section said the offline clean leaderboard is not tied, and concluded that
#45's ceiling is a gap between the offline policies and real models.

**That was wrong, and the way it was wrong is this project's own subject.**

Attributing the spread to an axis dissolves it. Per variant, 3 clean repeats
on `applicant.yaml`'s single scenario, 7 of 17 variants answer *differently
across repeats of the same scenario* — and a clean offline run has no fault
in it to vary. The cause is in `offline.py`, declared, forty lines above the
policies:

> Real agents are stochastic; perfectly deterministic stand-ins make top-1
> stability meaningless, because every seed produces an identical ranking
> and the gate can never fail. A seeded slip gives the offline path enough
> variance to exercise the stability machinery.

`SLIP_RATE = 0.15`, applied through `_slip` by **every** policy, at the
**same rate**. So the offline clean spread is a Bernoulli draw with one
parameter shared across all configurations. Those five values are five draws
from one distribution, not five configurations with different abilities.
They were never a measurement of discrimination, and "offline the board is
not tied" was a sentence about a noise generator.

What survives, and it is the useful half:

* Offline, clean discrimination exists **only as all-or-nothing by tool
  reach**. `toolset-records-partial` sits at exactly `0.0` on both fixtures,
  because it cannot see the records it would need. Everything graded above
  that is the slip rate.
* So the offline path has **no mechanism** by which a graded clean spread
  could appear, and a candidate fixture cannot be validated offline for the
  property #45 asks about. Not "would be a weak prediction" — there is no
  prediction to make.
* And the live tie in 003/007 is therefore the *first* measurement of this,
  not a disagreement with an offline one.

The 0.0-by-tool-reach case is also the one #45 already rules out: a config
that cannot in principle answer is a ceiling at zero, which is as tie-shaped
as a ceiling at one.

## What I would have missed

The rows were green-ish and self-consistent. `propagated: false` was
correct. `propagation_rate` was correct. The gate was correct. The only
thing wrong was a word in a column that gates nothing — which is precisely
the kind of defect that survives, because nothing fails when it is wrong.

And in the same write-up, I published a spread as a finding without asking
which axis it varied along. The answer was "none — it is a configured noise
rate." Caught by attributing it, which took one query and should have come
before the claim rather than after it.
