# 017 — Why no fixture discriminates on the clean run

**Status:** structural argument from evidence already paid for
**Cost:** $0.00
**Issues:** #45, #4

## The question

#45 asks for a fixture whose **clean** leaderboard separates competent
configurations, because #4's statistic needs two rankings and every live
matrix has produced one. Three fixtures have now been tried and all three
tie. This asks why, rather than asking for a fourth.

## What I had wrong

I spent part of today arguing the ceiling was a limitation of the scenario
model — that `expected_total` is a plain sum of declared integers, so the
clean task is always "add up what the tools return", and any competent
config does that.

**That is false, and `Scenario.expected` is the proof:**

```python
@property
def expected_total(self) -> int:
    return self.expected if self.expected is not None else sum(self.records.values())
```

A scenario can declare a correct answer that is **not** the sum of its
records. `discontinued.yaml` already does, on all three of its scenarios —
its answer is the *stocked* total, and records whose id ends
`__discontinued` must be excluded. That is a genuine clean-side reasoning
step, expressible today, and it was built for exactly this issue: the
fixture's own header calls it "the only bundled task on which the clean
leaderboard can discriminate."

So the model is not the constraint. The fixture #45 asks for has already
been attempted.

## Why that attempt failed, measured six times

`discontinued.yaml`'s `wide` scenario, every live probe of it:

| runs | prompt | model | clean | expected |
|---|---|---|---|---|
| 6 | `naive` | cheap ×4, smart ×2 | **668** | 668 |

The `naive` prompt — the one the fixture assigns the *careless* role, and
which the offline policy answers 808 with — excludes the discontinued lines
correctly, every time, at both model levels.

The discrimination was assigned to a **prompt level**, and a capable model
reads the task and does it properly regardless of what the prompt tells it
to be. That is experiment 003's lesson, and this is its sixth confirmation.

## The three levers, and why each collapses

For a clean leaderboard to separate *competent* configs, the task needs a
step a competent config might or might not take. Three things vary across
the grid:

| lever | what omission means | result |
|---|---|---|
| **tool grant** | the config *cannot* take the step | censored, or a ceiling at zero — which #45 rules out by name |
| **prompt** | the config was *told* not to bother | capable models ignore it and do the task right (003, six times over) |
| **model level** | the config *could not work it out* | the only lever left |

So the clean-discriminating fixture is one calibrated to the **capability
frontier**: hard enough that `cheap` fails and `smart` succeeds. Not a
reasoning step a prompt can be careless about, and not a tool one config
lacks.

`discontinued.yaml` at both levels gives 668, so it sits well below that
frontier. `applicant.yaml` gives 267700 on four configs ([016](../016-the-clean-tie-and-the-faulted-split/)),
so it does too.

## What that means for #45

Its request is not a design problem. It is a **calibration against a moving
target**, and three consequences follow:

1. **It cannot be designed offline.** Already established by the retraction
   in #45: the offline clean spread is one `SLIP_RATE` shared by every
   policy, so the offline path has no mechanism by which a graded clean
   spread could appear. Combined with this, there is nothing to aim with.
2. **It cannot be designed against a model you have not run.** The frontier
   is where a specific model stops succeeding, and finding it is a live
   search — cheap per probe now that the rotation varies the config, but a
   search nonetheless.
3. **It expires.** A fixture tuned to haiku-4-5's frontier is not tuned to
   its successor's. Any fixture that satisfies #45 has a shelf life, and
   #45's premise — a fixture that discriminates — is therefore not a
   property a fixture can hold permanently.

The honest conclusion: **#4's statistic is not merely unmeasured, it is
measurable only against a particular model at a particular time**, and the
bounded claim is the durable one:

> For tasks whose clean version any competent configuration solves,
> robustness is the only axis with discriminating power.

## What would actually settle it

A deliberate frontier search: probe one fixture at `cheap` and `smart`
across a difficulty ladder until the two levels disagree. The rotation can
do it at ~$0.05 a rung. That is a different issue from "build a fixture",
and it is the one worth filing — if a correlation coefficient for a single
model-month is worth having at all, which is a question about what the
project claims rather than about what it can measure.

## What I would have missed

I had written the structural argument the other way round — the scenario
model as the constraint — and it was wrong in a way that would have read
as rigorous. The refutation was one property in `spec.py` and one fixture
already in the repo, and I found it only because I went to check the claim
before publishing it rather than after.
