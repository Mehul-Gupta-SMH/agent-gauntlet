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

None of this has been re-run live. Until it is, the fixes are claims.

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

In CI, dispatch the `live-gauntlet` workflow and escalate `mode` one step at a
time: `smoke` → `probe` → `matrix`.
