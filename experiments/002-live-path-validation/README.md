# Experiment 002 — Live path validation

**Date:** 2026-09-15 · **Cost:** ~$0.05 across 4 CI dispatches · **Status:** complete

## Question

Does the live path work end to end against a real model — generated project →
CommonADK → SDK runner → tools through the interposer → trace → parsed answer
→ score?

Run in GitHub Actions ([`live-gauntlet.yml`](../../.github/workflows/live-gauntlet.yml)),
because the credential is a repository secret and is not available anywhere else.

## Method

Escalation in three stages, each cheaper than the one it protects:

| Mode | Cost | Proves |
|---|---|---|
| `smoke` | ~$0.0001 | the credential authenticates |
| `probe` | 1 agent run | the whole path, end to end |
| `matrix` | the real spend | the gate |

Target `langgraph`, model `claude-sonnet-5`.

## Results

### Stage 1 — smoke (run 1) ✅

```
stop_reason: end_turn
usage: 16 in / 4 out
text: ok
```

### Stage 2 — probe (runs 2–4)

**Run 2 — FAILED.** The agent behaved correctly:

```
tool calls : ['list_records', 'get_summary', 'fetch_record', 'fetch_record', 'fetch_record']
parsed total : None
```

That is precisely the `verifying` pattern — enumerate, cross-check, fetch every
record. But the answer parsed as unanswered, and the probe could not say why.

**Run 3 — FAILED, with the cause visible.** After adding trace diagnostics:

```
trace events : {'RunStarted': 1, 'AgentStarted': 1, 'LLMCall': 3,
                'ToolCall': 5, 'AgentFinished': 1, 'RunFinished': 1}
tokens/cost  : {'prompt_tokens': 3291, 'completion_tokens': 429,
                'total_tokens': 3720, 'cost_usd': 0.016308,
                'usage_complete': True, 'cost_complete': True}

final text (966 chars):
[{'signature': 'Ev8CCpAB…', 'thinking': '', 'type': 'thinking'},
 {'text': "…Sum of records: 37 + 12 + 58 = 107\nWarehouse-reported summary
   total: 107\n…\n\nTOTAL: 107\nANOMALY: no", 'type': 'text'}]
```

**Run 4 — PASSED.** `OK -- the live path works end to end.`

## Findings

### 1. `RunFinished.final_text` is not always text

On the langgraph target it carried `repr()` of the model's content-block list,
including a thinking block. The newlines were backslash escapes inside a Python
literal, so no line-anchored pattern could match — despite the model having
produced the requested output exactly.

The failure is quiet in the worst way: no exception, just a plausible string
that fails to contain what the caller expects. Reported upstream on #13.

### 2. The harness blamed the model for its own defect

Run 2's diagnostics concluded *"the model produced text but not in the required
format."* **That was false.** The model followed the contract perfectly,
cross-checked both sources, and reported the correct total of 107.

This is the single most important result of the experiment. A false finding
about instruction-following is exactly the class of error this project exists
to avoid making about other people's agents — and the harness made it about its
own. It is now a regression test built from the captured reply.

### 3. Two cheap checks caught two different defects

The probe proved the *path* but not the *grid*: a later matrix dispatch was
refused by the preflight because the default model grid spanned two providers
and only one credential was present. The probe had passed because it filters to
a single variant.

A cheap check does not subsume a cheaper one aimed elsewhere. The escalation
ladder needs to cover the fan-out, not just the depth.

## Verified by this experiment

- Generated `common/` projects load and run under a real SDK runner
- Tool calls route through the interposer during live execution
- Token counts, `cost_usd` and durations arrive complete (`usage_complete: True`)
- The structured answer contract parses once content blocks are flattened
- The preflight refuses an unrunnable grid before spending

## Still open

The **live matrix has not yet run**, so the M0 gate (#1) remains unanswered
against real agents.
