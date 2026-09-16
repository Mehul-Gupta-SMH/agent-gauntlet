# Experiment 001 — Offline harness validation

**Date:** 2026-09-15 · **Cost:** $0.00 · **Status:** complete

## Question

Does the measurement machinery grade, aggregate and gate correctly, on inputs
whose correct grading is known in advance?

This is **not** a question about agents. Scripted policies stand in for them so
every subsystem can be exercised without credentials or spend.

## Method

```bash
gauntlet run fixtures/inventory/task.yaml --out runs --repeats 2 --seeds 3
```

9 variants (2 models × 2 prompts × 2 toolsets, + 1 sentinel) × 3 scenarios ×
2 repeats × {clean, faulted} × 3 seeds = **324 runs**.

Task fingerprint `5c9848747223eaa4`, which pins the pre-registered gate
(tau ≥ 0.7, top-1 ≥ 0.6, seeds ≥ 3).

## Results

See [`console.txt`](console.txt), [`summary.json`](summary.json),
[`ledger-stats.json`](ledger-stats.json).

### Outcome distribution under fault (162 faulted runs)

> **Taxonomy note (2026-09-16).** These counts use the four-outcome
> taxonomy in force at the time. `detected_and_surfaced` no longer exists:
> detection was inferred from "the answer does not match the credulous
> figure", which any wrong answer satisfies. See #16 and
> [experiment 005](../005-fifth-outcome-live/). The numbers below are left
> exactly as measured; the ledger from this run predates the stored answer
> and so cannot be re-scored.

| Outcome | Count |
|---|---|
| `undetected_harmless` | 88 |
| `undetected_propagated` | 38 |
| `detected_and_surfaced` | 36 |

All 162 clean runs classified `clean`. Every run carried the same task
fingerprint, so the whole set was judged against one bar.

### Gate verdict

```
median tau      = 1.0
top-1 stability = 0%

GATE: FAIL
  - top-1 stability 0.00 < 0.6
```

**FAIL is the correct verdict here**, and the useful result. Scripted policies
are near-deterministic, so several variants tie at the top; a tied ranking has
no unique winner, and top-1 stability is therefore 0. The gate is doing exactly
what it exists to do — refusing to certify a measurement that does not
discriminate.

Note the pair of numbers: **perfect rank stability (tau 1.0) alongside zero
top-1 stability.** Read alone, tau 1.0 looks like a resounding pass. It is
vacuous: a metric that cannot separate variants cannot be unstable. This is the
concrete case behind the binary-vs-graded argument in #17, and the reason
undefined tau is treated as failure rather than success.

## What this establishes

- Scoring classifies all four terminal outcomes correctly (#16)
- Propagation is confined to credulous variants, and gates them
- Counterfactual clean/faulted pairs share a seed
- Every variant faces identical scenarios and fault seeds
- The sentinel ranks last — the instrument check (#29).
  **Superseded by [experiment 003](../003-live-m0-gate/):** it did rank last
  here, but the sentinel was degraded by a prompt asking it to be careless,
  and a scripted policy has no choice but to comply. Live, a capable model
  ignored the instruction and the sentinel ranked 6th of 9. This line
  validated the *scoring*, using a subject that could not disobey.
- The pre-registered gate renders a verdict and exits non-zero on failure

## What this does NOT establish

Nothing about real agents. Scripted policies have none of the variance that
makes the seed-stability question hard, which is the entire subject of #1. The
numbers above describe the harness.
