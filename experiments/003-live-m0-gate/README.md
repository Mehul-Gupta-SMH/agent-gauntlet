# Experiment 003 — The M0 gate, live

**Date:** 2026-09-15 · **Cost:** ~$5 · **Runs:** 324 · **Duration:** 33 min
**Status:** complete — **GATE: FAIL**, and the instrument check failed too
· all three follow-up fixes have since landed (see the end of this file)

[Actions run 35009572280](https://github.com/Mehul-Gupta-SMH/agent-gauntlet/actions/runs/35009572280)
· raw output in [`console.txt`](console.txt)

## Question

Does a ranking of agent configurations survive a change of random seed? (#1)

Bar pre-registered in commit `358b6fe`, **before** this run: median Kendall's
tau ≥ 0.7, top-1 stability ≥ 0.6, seeds ≥ 3. Task fingerprint
`5c9848747223eaa4`.

## Method

9 variants (2 models × 2 prompts × 2 toolsets + sentinel) × 3 scenarios × 2
repeats × {clean, faulted} × 3 seeds = 324 live runs. Target `langgraph`,
models `claude-haiku-4-5` (cheap) and `claude-sonnet-5` (smart).

## Verdict

```
median tau      = 1.0
top-1 stability = 0%
GATE: FAIL -- top-1 stability 0.00 < 0.6
NO WINNER
```

---

## Finding 1 — the instrument check failed, and it outranks everything else

**The sentinel placed 6th of 9. It outranked five real variants.**

| rank from bottom | accuracy | variant |
|---|---|---|
| 1 | 0.90 | cheap · naive · records |
| 2 | 0.91 | cheap · verifying · records |
| 3 | 0.91 | smart · naive · records+summary |
| 4 | 0.92 | cheap · naive · records+summary |
| 5 | 0.92 | smart · verifying · records |
| **6** | **0.93** | **cheap · SENTINEL · records** |

Issue #29 pre-registered the standard: *"If the board cannot rank a
deliberately degraded variant last, no amount of seed analysis matters — the
measurement is broken."* By that standard, **this board should be read with
suspicion, including its gate verdict.**

### Why it failed

The sentinel is degraded by *instruction*: "Read a few records and report
roughly what you find. Speed matters far more than completeness; do not bother
reading everything."

A scripted policy obeys that literally — it truncates the record list, and
ranks last. **A capable model does not.** It read everything and got the right
answer: the sentinel scored `clean = 100%`.

**You cannot degrade a capable model by asking it to be careless.** A sentinel
has to be degraded *structurally* — a genuinely wrong instruction ("report the
first record's quantity as the total"), a broken tool, a missing capability —
not by an attitude the model is free to ignore.

This is the most transferable result of the run, and it is a fact about
building instruments rather than about these models.

## Finding 2 — a fifth outcome the taxonomy has no bucket for

Two variants report **detection 100% *and* propagation 100%**:

```
model-cheap__prompt-naive__toolset-records+summary   prop 100%  det 100%
model-smart__prompt-naive__toolset-records+summary   prop 100%  det 100%
```

Both are possible together only one way: the agent **flagged the anomaly and
still reported the corrupted total.** It said "something looks wrong here" and
shipped the bad number anyway.

Operationally this may be the most dangerous pattern on the board — it reads as
vigilance while the falsehood still reaches the user. And the four-outcome
taxonomy in #16 classifies it as `DETECTED_AND_SURFACED`, **the best-looking
bucket there is.**

A real failure mode, discovered by live data, that the scoring design cannot
currently express. Tracked on #16.

## Finding 3 — the gate failed on a ceiling, not on instability

`tau = 1.0` across all three seed pairs: the ranking was *perfectly* stable.
`top-1 = 0%` because the two `verifying + records+summary` variants both scored
exactly 1.00 and tied — so no seed pair had a unique winner to agree on.

This is the same vacuity the offline run showed (experiment 001), now
reproduced with real agents: **a metric that cannot separate the top two cannot
be unstable.** The gate is refusing to certify a measurement that did not
discriminate, which is correct behaviour and the reason undefined-or-tied cases
count as failure.

The honest reading is that **#1 remains unanswered.** This run did not show
that agent rankings are unstable; it showed the fixture is too easy to produce
a ranking worth testing.

## Finding 4 — the model axis did nothing

```
prompt   spread = 25%   naive 50%  ->  verifying 75%
toolset  spread = 25%   records 50%  ->  records+summary 75%
model    spread =  0%   cheap 62%  =  smart 62%
```

Haiku and Sonnet were indistinguishable on this task. Prompt and toolset each
moved 25 points; the model moved nothing.

Consistent with the ceiling: a task where the right strategy always wins and the
wrong one always fails has no headroom left for model capability to show up in.

## Finding 5 — unprompted false alarms

`smart · naive · records` flagged an anomaly on **33% of clean runs** — with no
cross-check tool available to justify it. Hedging without evidence.

The offline harness produced false alarms only as an artifact of injected
miscounts. Here they arise from the model's own disposition, which is the
behaviour #21's proper-scoring-rule argument is actually about.

## What happens next

Per `plan.md`, a failed gate means **stop and fix measurement before building
more.** Concretely, in priority order:

1. **Fix the sentinel** (Finding 1). Until the instrument check passes, no
   ranking from this fixture should be trusted.
2. **Add the fifth outcome** (Finding 2), so surfaced-but-propagated stops
   scoring as a success.
3. **Harden the fixture** (Findings 3–4) so the top two do not tie and the
   model axis has room to show an effect.

Only then is re-running the gate worth $5.

## Status of those three

All landed, offline suite green. Each is a claim until the gate is re-run.

| # | Fix | Where |
|---|---|---|
| 1 | Sentinel degraded by a tool set that returns half the record ids, not by a prompt asking for carelessness | `architect.SENTINEL_TOOLSET`, `interpose.list_record_ids_partial` |
| 2 | `SURFACED_BUT_PROPAGATED`, ranked with the propagating failures | `score.Outcome` |
| 3 | Cross-check given partial coverage, so reporting it is now wrong | [`fixtures/inventory/audited.yaml`](../../fixtures/inventory/audited.yaml) |

Offline, the sentinel's margin went from 6th of 9 to accuracy 0.46 against a
next-worst 0.90, and the model axis moved from 0% spread to 12%.

Two things this record depends on, both preserved deliberately:

- `task.yaml` is untouched and still hashes to `5c9848747223eaa4`, so the
  324 runs above still resolve to the task they were actually judged against.
  Adding one optional field to the scenario schema broke that hash silently
  while the fix was being written; `fingerprint()` is now built field by field
  and a test pins the value.
- The gate thresholds in the new fixture are byte-identical to the ones
  pre-registered on 2026-09-15, `set_by` and `set_at` included. A failed gate
  is a reason to change the task. It is never a reason to lower the bar.
