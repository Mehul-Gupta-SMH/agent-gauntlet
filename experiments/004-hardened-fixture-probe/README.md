# Experiment 004 — Does the hardened fixture work on a real model?

**Date:** 2026-09-16 · **Cost:** $0.0199 (one run) · **Status:** complete — **PASS**

[Actions run 35059214803](https://github.com/Mehul-Gupta-SMH/agent-gauntlet/actions/runs/35059214803)
· raw output in [`console.txt`](console.txt)

## Question

Experiment 003's three fixes were claims. This asks the cheapest falsifiable
part of one of them: on a real model, does a partial audit actually produce
reconciliation — or does the model just report the audited figure?

It matters before the matrix, not after. If the reconciliation instruction does
not land, every verifying variant undercounts by the same amount, the prompt
factor measures nothing, and the ~$6 is spent producing a board that cannot
answer #1 for a second time.

## Method

One live run, `claude-sonnet-5` on target `langgraph`, scenario `partial`
(4 records, audit covers 2 — 95 of 128), no fault injected. Run in CI by
[`live-probe.yml`](../../.github/workflows/live-probe.yml), which now fires
automatically on every code push to `main` and daily.

## Result — the reconciliation lands

```
tool calls : ['list_records', 'list_audited_records',
              'fetch_record' x4, 'get_summary']
expected   : 128
parsed     : 128
ANSWER: correct -- the grand total.
```

The model's own words:

> The audit covers widget_a and widget_c. My sum for those two records is
> 37 + 58 = 95, which matches the warehouse's audited total of 95 — so my
> per-record data is consistent with the audit.
>
> Total across all records: 37 + 12 + 58 + 21 = 128

That is exactly the four-step behaviour the fixture was designed to require:
enumerate everything, discover the audit's coverage, reconcile that subset,
then report the grand total rather than the audited figure. The new
`list_audited_records` tool is reachable through CommonADK on a real SDK, and
`usage_complete`/`cost_complete` both arrive true.

**What this does not establish.** One clean run on the easiest of the three
scenarios, with no fault injected. It says the path works and the instruction
lands. It says nothing about whether the fixture separates the top two
variants — that is still the matrix's job, and #1 is still unanswered.

## Finding — the probe's first automatic run went green having called no model

The run before this one ([35057983880](https://github.com/Mehul-Gupta-SMH/agent-gauntlet/actions/runs/35057983880))
passed in 1 second. `live-probe.yml` installed the package but not the
target's SDK, so the run died on `ModuleNotFoundError: No module named
'langchain_core'` — and the probe reported **"the model was unreachable …
not a build failure"**, exited 0, and the build went green.

The classifier decided that by *absence*: nothing had reached `_final_text`,
so the call never came back, so it must be the network. A missing import
satisfies that test exactly as well as an outage does.

The commit that introduced it argued this was safer than matching exception
types. It produced the failure it was written to prevent, one commit later.

**A false green is worse than the false red it was avoiding, because nobody
investigates a pass.** Code 4 is now earned rather than assumed: an import,
type, attribute or OS error anywhere in the cause chain is ours whatever the
message says; a positively provider-shaped type or message earns 4; anything
unrecognised is ours and goes red.

That makes three for three. Every defect this project has found in itself
presented as a plausible pass rather than an error — the harness blaming the
model in 002, the sentinel a capable model ignored in 003, and now a probe
that measured nothing and said so cheerfully.

## What this unblocks

The matrix. At $0.0199 per run, 324 runs is roughly **$6.50**.
