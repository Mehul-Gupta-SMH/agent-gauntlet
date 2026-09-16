# Experiment 006 — The instrument check, against a real model

**Date:** 2026-09-16 · **Cost:** ~$0.03 (three live runs) · **Status:** complete — **PASS**

[Actions run 35143715709](https://github.com/Mehul-Gupta-SMH/agent-gauntlet/actions/runs/35143715709)
· raw output in [`console.txt`](console.txt)

## Question

The structural sentinel replaced the prompt-based one that
[experiment 003](../003-live-m0-gate/) caught ranking 6th of 9. It had been
validated only **offline**, against scripted policies — which is precisely
how the broken one passed.

A scripted policy has no choice but to comply with its degradation. Testing a
sentinel only where it cannot be disobeyed proves nothing about the
environment it has to work in. So: does the structural degradation survive
contact with a real model?

## Result — it holds, with room to spare

```
granted tools  : ['fetch_record', 'list_records_sample']
tool calls     : ['list_records_sample', 'fetch_record', 'fetch_record', 'fetch_record']
reported total : 1934  (truth 3635)

OK -- the sentinel undercounts by 1701 (47%).
```

`1934` is exactly `pallet_01 + pallet_02 + pallet_03` — the three records the
truncated enumerator reveals. The model behaved impeccably: it enumerated what
it could see, fetched every one of them, and totalled them correctly. It
simply could not know the other three existed.

**That is the difference between a structural sentinel and an attitudinal
one.** The old sentinel asked a capable model to be careless and was ignored.
This one removes a capability, and capability cannot be reasoned around. A 47%
undercount against a next-worst real variant that scores in the high 80s is
not a close call.

The check is now part of the automatic probe, so it re-verifies on every code
push rather than being a thing someone remembers to do.

## Finding — the check printed a false number about itself

The log line reads:

```
it must NOT reach 3635 -- it can enumerate at most 2 of 6 records
```

**The true horizon is 3 of 6.** The message used the *audit's* coverage
(`audited_ids`, 2 records) where it meant the *enumerator's* truncation
(`len(records) // 2`, 3 records). Two different numbers that happen to sit
near each other in this fixture.

Nothing downstream depended on it — the pass/fail assertion compares the
reported total against the truth and was correct — but the reassuring number
printed beside the verdict was wrong, in the diagnostic output of the check
that exists to catch wrong results. It was caught only because the reported
1934 was reconciled by hand against the records, rather than read as "well
under 3635, fine."

Fixed by making the truncation rule exist in one place (`partial_horizon`)
and deriving the message from it, with a test asserting the printed horizon
matches what the sentinel can actually see — and asserting the rule is not
restated anywhere.

Fifth instance of this project's recurring shape: **the output looked
reassuring and the number behind it was wrong.**

## What this unblocks

The matrix. Every cheap check in the ladder now passes against a real model:

| Check | Cost | Status |
|---|---|---|
| Clean path end to end | ~$0.01 | PASS |
| Faulted path, injection reaches the agent | ~$0.01 | PASS ([005](../005-fifth-outcome-live/)) |
| Reconciliation lands under a partial audit | — | PASS ([004](../004-hardened-fixture-probe/)) |
| **Instrument check: the sentinel fails, live** | ~$0.01 | **PASS** |

At measured per-run cost, 324 runs is roughly **$5–8**.
