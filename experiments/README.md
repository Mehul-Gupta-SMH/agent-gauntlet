# Experiments

Every run that produced a result, with its raw output committed alongside the
writeup. A number quoted in the README or an issue should be traceable to a
file in here.

Two rules this directory follows:

- **Captured, not transcribed.** `console.txt` and `summary.json` are the
  actual artifacts. Prose in a writeup can be wrong; the artifact next to it
  cannot be quietly edited to agree with it.
- **Negative results are results.** An experiment that failed its own gate, or
  that found the harness at fault, stays in the record at the same weight as
  one that passed.

| # | Experiment | Cost | Outcome |
|---|---|---|---|
| [001](001-offline-harness-validation/) | Offline harness validation | $0.00 | Machinery correct; **gate FAILs** on a tied ranking, which is the right verdict |
| [002](002-live-path-validation/) | Live path validation | ~$0.05 | Path works end to end; found two defects that would each have voided a matrix |
| [003](003-live-m0-gate/) | The M0 gate, live | ~$5 | **Gate FAILs**, and so does the instrument check — the sentinel ranked 6th of 9 |
| [004](004-hardened-fixture-probe/) | Does the hardened fixture work live? | $0.02 | **PASS** — the reconciliation lands on a real model; found a probe that went green having called nothing |

## Where that leaves the question

*Does a ranking of agent configurations survive a change of random seed?* (#1)
is **still unanswered.** Experiment 003 ran the matrix against real agents, but
the fixture could not separate its top two variants and the sentinel did not
rank last, so the board it produced cannot be used to answer anything.

The bar was pre-registered first, in
[`fixtures/inventory/task.yaml`](../fixtures/inventory/task.yaml) and commit
`358b6fe`: median Kendall's tau ≥ 0.7, top-1 stability ≥ 0.6, seeds ≥ 3. It is
covered by the task fingerprint (`5c9848747223eaa4`), so moving it after the
fact would change the hash that every run record carries. It has not been
moved.

### The three fixes 003 called for, and where they landed

A failed gate means fix the measurement, not lower the bar. All three are in,
with the offline suite green:

1. **The sentinel is degraded structurally.** The prompt asking it to be
   careless is gone; it now runs an ordinary `naive` prompt on a
   `records-partial` tool set whose enumeration tool silently returns half the
   record ids. A model cannot reason its way to records it cannot see. Offline
   the margin went from "6th of 9" to accuracy 0.46 against a next-worst 0.90.
2. **`SURFACED_BUT_PROPAGATED` exists**, and ranks with the propagating
   failures rather than the detecting successes.
3. **The fixture has a ceiling no more.**
   [`audited.yaml`](../fixtures/inventory/audited.yaml) gives the cross-check
   *partial* coverage, so it verifies a subset instead of being the answer.
   Reporting it — the shortcut that used to score exactly 1.00 — is now wrong.

**The bar is byte-identical in the new fixture**, set_by and set_at included;
only the task changed, and the fingerprint moved with it (`d11a0b2a6801ee74`),
which is what the fingerprint is for. `task.yaml` still hashes to
`5c9848747223eaa4`, so experiment 003's records still resolve to the task they
were actually judged against — and that is pinned by a test, because adding one
optional field to the schema silently broke it once already.

[Experiment 004](004-hardened-fixture-probe/) tested the cheapest falsifiable
part of fix 3 for $0.02: on a real model the partial audit does produce
reconciliation, and the answer was the grand total rather than the audited
figure. The remaining claims need the matrix.

## Running one

```bash
# offline -- no credentials, no spend
gauntlet run fixtures/inventory/audited.yaml --out runs --repeats 2 --seeds 3

# live -- needs ANTHROPIC_API_KEY; prove the path first
gauntlet probe fixtures/inventory/audited.yaml --target langgraph
gauntlet run fixtures/inventory/audited.yaml --out runs --live \
  --target langgraph --repeats 2 --seeds 3
```

`fixtures/inventory/task.yaml` is the original and stays runnable, so
experiment 003 can be reproduced exactly. It is not the fixture to gate
against: its cross-check is the answer, so the top variants tie at 1.00 and
top-1 stability is undefined by construction.

### The escalation ladder, and what runs by itself

| Step | Cost | Proves | When |
|---|---|---|---|
| offline suite (`ci.yml`) | $0 | the machinery, on every Python we support | every push and PR |
| `probe` (`live-probe.yml`) | ~$0.02 | the whole live path, clean **and** faulted | **automatically**, on every push to `main` that touches code, plus daily |
| `matrix` (`live-gauntlet.yml`) | ~$5 | the gate | manual dispatch only |

Only the last one is manual, because only the last one is expensive. Dispatch
`live-gauntlet` with `mode: matrix` when the probe is green.

The daily probe is not redundant with the per-push one: it catches drift no
commit of ours causes — a model update, a CommonADK release, an SDK moving
where the final text lives. Both defects experiment 002 found were of exactly
that shape, and both were silent.

The probe runs two live calls on the *hardest* scenario, not the first one:
a clean run and a faulted one. The faulted half exists for a failure that is
otherwise invisible until the matrix has spent — if an injected falsehood
never reaches the agent, every propagation and detection number on the board
is a confident zero and the board looks immaculate. Whether a tool call
returned a faulted result is decidable without the model's cooperation, so
that half is a hard failure rather than a judgement call. `--no-faulted`
halves the cost and the coverage.

The probe never fails the build for something a contributor cannot fix. It
exits 4 when the model is unreachable (outage, rate limit, revoked key) and CI
turns that into a warning; a missing secret skips the job entirely. Only a
genuinely broken path goes red. That distinction is the whole reason the probe
can be automatic at all — `ci.yml` spent several days red because a
pre-registered gate FAIL, which is a measurement, was being read as a build
failure.
