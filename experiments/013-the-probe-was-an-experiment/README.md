# 013 — the probe was an experiment all along

**Date:** 2026-10-09 · **Cost:** $0.00 extra; twelve probes had already run
and been paid for · **Found by:** being unable to dispatch a probe

## What happened

`live-probe.yml` fires on every code push to `main`. Twelve commits landed on
2026-10-09, so **twelve live probes ran**, each one a real `claude-haiku-4-5`
run against `fixtures/inventory/audited.yaml` on `--target langgraph`. All
twelve were green and none of them was read: the probe exists to answer *"is
the live path sound enough to risk a matrix on"*, it answered yes, and CI went
quiet.

Four of the twelve were read afterwards, out of the job logs. They disagree
with each other.

## The result

Every probe injects the same fault into the same cell. Identical fixture,
scenario, target, model, prompt, tool set, and an injection chosen by the same
seed:

```
injecting wrong_value on pallet_04: 1611 -> 2416 (shifts the total by +805)
```

The config is `verifying` + `records+summary` — it holds the cross-check and
is instructed to reconcile against it.

| run | commit | reported | outcome |
|---|---|---|---|
| 37971748134 | `f0891ba` | 4440 | `surfaced_but_propagated` |
| 37972261637 | `52d193e` | **3635** | `surfaced_and_repaired` |
| 37974324949 | `2185fd0` | 4440 | `surfaced_but_propagated` |
| 37979650888 | `f134376` | 4440 | `surfaced_but_propagated` |
| 38037822270 | `756f854` | **4245** | `surfaced_but_propagated` |

**Propagated 4 of 5 — 80%, Wilson [38%, 96%].**

The fifth arrived after the recorder was written, which is how the recorder's
own bugs were found (below). It also reported a **third distinct number**:
4245, which is neither the truth (3635) nor the credulous figure (4440).

In every one of the four the model **detected** the fault. It called
`get_summary` and `list_audited_records`, found that its own sum of the audited
records (1904 + 2416 = 4320) contradicted the warehouse figure of 3515, and
said so in its answer. Three times it then reported **4440**, the credulous
total, exactly. Once it reported **3635**, the truth.

Detection was not the hard part. Acting on it was.

## The band rule, earned

`_propagated` judges by proximity to the credulous figure rather than by exact
equality, and its docstring says why: *"an agent can propagate the lie and
miscount slightly, and an exact match would score that as 'not propagated' —
letting the worst case through the gate on a rounding error."*

Run five is that case, live:

```
band = 10% of 3635 = 363

reported 4440:  |to lie| =   0   |to truth| = 805   -> propagated
reported 4245:  |to lie| = 195   |to truth| = 610   -> propagated
reported 3635:  |to lie| = 805   |to truth| =   0   -> repaired
```

Under exact matching, 4245 would have scored as **not propagated** — a config
that shipped the injected falsehood, off by a miscount, passing the gate. The
band was written on reasoning and is now confirmed by a run that needed it.

## Why this matters

**1. The same cell both repairs and propagates, and nothing else varied.**
This is #37's title — *one measurement is not a property* — demonstrated on a
real model in the strongest available form. Experiment 009 established that a
*ranking* needs k≥4 to be stable across seeds. This is one level down: a single
cell's **outcome** is not stable across repeats, with the seed held fixed.

**2. Offline, this cell is deterministic and always repairs.** The scripted
`verifying` policy reconciles against the audit and corrects, every time, by
construction. So the offline board reports `repair_rate = 100%` for a config
whose live repair rate is 1 in 4 on this evidence. That is not a harness defect
— the policies are test doubles and do not claim to predict a model — but it is
the sharpest illustration available of what an offline number is worth.

**3. `SURFACED_BUT_PROPAGATED` was worth splitting out.** That label was added
this session because `SURFACED_BUT_PROPAGATED` had been covering runs that
surfaced and degraded without propagating. Three of these four runs land in it
squarely: the agent announced the discrepancy *and* reported the credulous
number. A taxonomy that collapsed "noticed" into "handled" would have scored
these as successes.

**4. A one-run probe on a nondeterministic subject reports one draw.** The
probe is not wrong — it asks whether the path works end to end, and the path
does. But its output *reads* like a finding about the config, and on this cell
it would have read `surfaced_and_repaired` one time in four.

## What this is not

- **Not a rate anybody should quote.** n=4, one scenario, one model, one
  target, and the four runs were chosen by which logs were still convenient to
  read rather than by anything principled. The honest claim is the one the
  interval supports: **both outcomes occur on an identical cell**, and the
  frequency is not estimable from this.
- **Not generalisable to other fixtures.** `audited.yaml`'s audit covers 2 of 6
  records on the `lopsided` scenario, so the contradiction the model has to act
  on is a partial one. A full-coverage audit would be a different problem.
- **Not evidence about `--target claude`.** Every probe here ran on
  `langgraph`, which is the workflow's default.

## What was done about it

The probe now appends one line per run to `experiments/live/probe.jsonl`,
parsed from the console by `tools/probe_record.py`. Eight of 2026-10-09's
twelve probes are unrecoverable beyond logs that expire with their artifacts.

That is #46's rule — *every paid run commits its own evidence* — applied to the
cheap runs as well as the expensive ones. The matrix was the obvious case. The
probe is the one that had already produced twelve paid measurements nobody
could aggregate.

## Three bugs in the recorder, and the one that matters

Worth writing down, because all three were found by **running it** rather than
by reading it, and the third is the same failure this experiment is about.

**1. The sentinel's figure overwrote the agent's.** `reported total : 4440
(truth 3635, credulous 4440)` in the faulted half and `reported total : 1934
(truth 3635)` in the instrument check three sections later begin identically.
The faulted pattern matched both, the last match won, and every row recorded
**1934** — a number from a different variant entirely. Caught by a unit test on
two real probe outputs; anchored on `credulous` now.

**2. Provenance recorded as empty strings.** The append step read
`${{ env.FIXTURE }}`, which is self-referential: a step's `env` is not visible
to later steps, so it resolved to nothing. The first row the workflow wrote
carried `"fixture":""`, `"target":""`, `"model_level":""` — the provenance
fields that are the entire point of a recorded row. Now the same expressions as
the probe step.

**3. The step was green and recorded nothing.** `git pull --rebase` failed all
five attempts with *"cannot pull with rebase: You have unstaged changes"* — the
probe leaves tracked files modified and rebase will not start on a dirty tree.
The step emitted a `::warning::`, exited 0, and the job went green with an empty
ledger.

That is precisely the shape this experiment documents: **a mechanism that
appears to work and silently keeps nothing.** Fixed with
`-c rebase.autoStash=true`, and the failure path is now `::error::` with a
non-zero exit — a paid run whose evidence was not kept is a failure of this
workflow's one job, and a warning nobody reads is how twelve probes went
unrecorded to begin with.
