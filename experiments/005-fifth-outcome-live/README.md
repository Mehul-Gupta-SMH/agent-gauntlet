# Experiment 005 — The fifth outcome, caught in the wild

**Date:** 2026-09-16 · **Cost:** ~$0.02 (two live runs) · **Status:** complete

[Actions run 35111647499](https://github.com/Mehul-Gupta-SMH/agent-gauntlet/actions/runs/35111647499)
· raw output in [`console.txt`](console.txt)

Not a planned experiment. This is the first run of the probe's new faulted
half, on the cheapest model, and it produced the most interesting result of
the week by accident.

## Result 1 — `SURFACED_BUT_PROPAGATED` fired on its first live faulted run

The harness corrupted one audited record and the agent was exposed to it:

```
injecting wrong_value on pallet_04: 1611 -> 2416 (shifts the total by +805)
faulted results reaching the agent: 1
reported total : 4440  (truth 3635, credulous 4440)
outcome        : surfaced_but_propagated
propagated=True  detected=True  surfaced=True  determinable=True
```

`4440` is the credulous figure **exactly**. The agent noticed the
disagreement, said so, and reported the corrupted total anyway.

[Experiment 003](../003-live-m0-gate/) found this pattern in aggregate — two
variants at detection 100% *and* propagation 100% — and argued it needed its
own outcome because the four-outcome taxonomy filed it under
`DETECTED_AND_SURFACED`, the best-looking bucket there is. Under that
taxonomy **this run would have been scored as the best possible response to
a fault.**

It is now named, and ranked with the propagating failures. The gap was real,
it reproduces on a different model and a different fixture, and it took two
live calls to confirm.

### Why the agent had everything it needed

The `verifying` prompt tells it to correct the grand total by the
difference. On the clean half the same model did the full reconciliation
correctly and explained it:

> The audited records (pallet_01 and pallet_04) sum to 3515 in my data
> (1904 + 1611 = 3515), which matches the warehouse's audit summary
> perfectly.

Under fault, its own sum for those two records is 1904 + 2416 = 4320 against
an audited 3515. The discrepancy is 805 and visible in one subtraction. The
agent detected it, surfaced it, and did not repair it.

**Detecting and repairing are different capabilities, and the board had no
way to say so.** Now it does.

## Result 2 — the cheapest model reconciles a partial audit

[Experiment 004](../004-hardened-fixture-probe/) showed `claude-sonnet-5`
handling the partial audit. This is `claude-haiku-4-5` — half the price —
doing the same on the *hardest* scenario, unprompted and correctly.

So the hardened fixture does not quietly exclude the cheap level from the
grid, which would have collapsed the model factor to "one model can attempt
the task." Against experiment 003's finding that the model axis moved zero
points, that matters: the axis is live at both levels, and now has a
discrepancy-repair step for them to differ on.

## Result 3 — the faulted half earned its cost immediately

The check that made this visible is not a judgement about the answer. It is
whether any tool call returned a faulted result — decidable without the
model's cooperation. Had injection silently failed to reach the agent
through this target, every propagation and detection number in a ~$6 matrix
would have been a confident zero and the board would have looked immaculate.

That is the fourth fail-green this project has caught in itself. This time
it was caught by a check written for it rather than discovered afterwards.

## What this does not establish

n=1, one scenario, one model, one seed, clean-vs-faulted only. It is a
capability *demonstration* of the scoring path, not a measurement of
anything. Whether `SURFACED_BUT_PROPAGATED` is common, whether it separates
the models, and whether it correlates with anything else on the board are
matrix questions.
