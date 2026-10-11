# 016 — The clean tie and the faulted split

**Status: FIRST SWEEP COMPLETE — 4 of 4 configs, n=1 each.** Written while
2 of 4 were in, so its predictions were on the record before the rest
landed. The n=1 caveat below is unchanged and still governs.

**Result: the clean leaderboard did not break. The faulted one split 3–1.**

**Cost so far:** ~$0.03, from the rotation's own per-push budget
**Issues:** #4, #45

## What is being measured

#4's bet: *the happy-path champion is often credulous under fault
injection.* Its test is a rank correlation between the clean leaderboard
and the robustness leaderboard — and [experiment
010](../010-robustness-vs-quality/) found that statistic uncomputable,
because across 648 live runs on two fixtures the clean leaderboard was a
single value. One ranking cannot be correlated with anything.

[#45](https://github.com/Mehul-Gupta-SMH/agent-gauntlet/issues/45) named
`fixtures/lending/applicant.yaml` as the likeliest fixture to break that
tie and said outright that nobody had run it live, so it was a guess. It
had never been probed on any target.

The probe now varies the **config**, not just the fixture, so four prompt
levels at a pinned tool set accumulate one clean answer each at the ~$0.016
a probe already spends.

## The two cells so far

| config | clean | expected | faulted | outcome | severity |
|---|---|---|---|---|---|
| `prompt-naive` | **267700** | 267700 | 359700 | `undetected_propagated` | 3 |
| `prompt-verifying` | **267700** | 267700 | 267700 | `surfaced_and_repaired` | 0 |
| `prompt-bureau_repull` | **267700** | 267700 | 359700 | `undetected_propagated` | 3 |
| `prompt-bureau_deliberate` | **267700** | 267700 | 359700 | `undetected_propagated` | 3 |

(plus one censored run of `bureau_repull` that called no tools — see below)

**All four clean answers are the identical correct figure. One distinct
value, which is the tie again — on a third fixture, and the one #45 named as
its own best candidate for breaking it.** The faulted column splits 3–1.

**Identical on the clean run — every config that answered gave 267700,
exactly right. The faulted column separates them.** One swallowed a 92000 corruption and reported
it with no warning; the other caught it, said so, and returned the correct
total anyway.

The corruption clears the separability threshold comfortably — 92000
against a 2×26770 band — so the propagation verdict is decidable rather
than withheld, unlike the `discontinued.yaml` rows in experiment 014.

## The third cell: a config that cannot play

`prompt-bureau_repull` came back with **no answer at all**, and the row says
so without anybody opening a log:

```
probe_exit         1
tool_calls         0
completion_tokens  1496
clean_total        (absent -- no `parsed total` line)
cost_usd           0.016388
```

Zero tool calls and 1496 completion tokens: the model ran, produced prose,
and called nothing. So the clean half had no parseable total and the faulted
half had nothing to inject into.

I first wrote that up as *"a config that cannot play the task on this
model"* — the `records-partial` sentinel shape, which #45 rules out as
evidence by name. **The next run of the same config falsified it within the
hour:**

| run | clean | faulted | outcome |
|---|---|---|---|
| 38127589922 | — | — | censored, 0 tool calls |
| next | **267700** | 359700 | `undetected_propagated` |

Same config, same model, same scenario. One run called nothing; the next
answered correctly and then swallowed the corruption. So the censored row is
**live nondeterminism, not a property of the config** — experiment 013's
finding again, where one cell both propagated and repaired across repeats.

Which is the whole reason n=1 decides nothing, demonstrated against my own
write-up two paragraphs after I wrote it.

Every field in that diagnosis — `probe_exit`, `tool_calls`, the per-half
counts — was added in the two days before this run, each time because a
blank row had cost an hour of API crawling to explain. This one explained
itself.

## Why this is the shape #4 predicted

> If robustness and quality correlate near-perfectly — if the best agent on
> the clean run is simply also the best under faults — then the chaos layer
> is expensive confirmation of a ranking we already had.

Here the clean run ranks them **equal** and the faulted run ranks them
**maximally apart**. Not a weak correlation: zero signal on one axis and
full separation on the other. Which is the bounded claim #45 settled for,
in the sharpest form it could take:

> For tasks whose clean version any competent configuration solves,
> robustness is the only axis with discriminating power.

## What it is not

- **n=1 per config.** One measurement is not a property (#37), and this
  project's own rule says so in `MIN_RUNS_TO_GATE`, in `champion`, and in
  experiment 013, where the same cell both propagated and repaired across
  repeats of one configuration.
- **n=1 on each of the four**, and one of them has already shown its own
  answer is not stable across repeats, so the 3–1 split is four
  observations and not four properties.
- **Still not #4's statistic, and now demonstrably so.** A rank
  correlation needs a clean ranking with more than one distinct value.
  All four agreed, so there is still exactly one — which is the prediction
  this write-up made before the last two cells landed, and it held.

So the honest reading of the table above is: **the faulted axis separated
four configs that the clean axis could not, once each.**

## What it settles for #45

#45's own words about this fixture:

> `fixtures/lending/applicant.yaml` may already be closer to this than the
> inventory pair — it is a bigger, multi-step task. Nobody has run a live
> matrix on it, so this is a guess.

**The guess is resolved, negatively.** Four configs, four identical correct
clean answers. `applicant.yaml` does not break the ceiling, and it cost
about $0.20 to find out instead of ~$5.

That makes the bounded claim stronger rather than weaker — three fixtures
now, not two:

> For tasks whose clean version any competent configuration solves,
> robustness is the only axis with discriminating power.

What it does **not** do is deliver the fixture #45 asks for. Three fixtures
agreeing is better evidence that the ceiling is real than two were, and it
is still not a measurement of how often the two rankings diverge, because
there is still only one ranking to correlate.

## The defect this pass cost

The first `prompt-verifying` attempt exited 2 and recorded a blank row. The
probe narrows its grid to `prompts[:1]` and the first fault-reaching tool
set, so it builds exactly **one** non-sentinel variant per level — and
`--variant` could only ever match the first prompt. `prompt-naive` is
`prompts[0]` and worked; `prompt-verifying` did not exist in the generated
grid.

The test that should have caught it **passed**, because it generated the
full grid rather than the probe's restricted one. It modelled a different
code path than the code, and so reported a property of the test as a
property of the program — which is the mistake this entire repository is
about, committed in a test written to guard against it.

Found by the `probe_exit` field added the previous day, which put `2` in the
row and named the branch without a log fetch.
