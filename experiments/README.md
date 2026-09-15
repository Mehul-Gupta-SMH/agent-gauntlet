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

## Not yet run

**The M0 gate against real agents.** Everything so far validates the
instrument. The question the project exists to answer — *does a ranking of
agent configurations survive a change of random seed?* (#1) — needs a live
matrix run, and has not been answered.

The bar was pre-registered first, in
[`fixtures/inventory/task.yaml`](../fixtures/inventory/task.yaml) and commit
`358b6fe`: median Kendall's tau ≥ 0.7, top-1 stability ≥ 0.6, seeds ≥ 3. It is
covered by the task fingerprint (`5c9848747223eaa4`), so moving it later would
change the hash that every run record carries.

## Running one

```bash
# offline -- no credentials, no spend
gauntlet run fixtures/inventory/task.yaml --out runs --repeats 2 --seeds 3

# live -- needs ANTHROPIC_API_KEY; prove the path first
gauntlet probe fixtures/inventory/task.yaml --target langgraph
gauntlet run fixtures/inventory/task.yaml --out runs --live \
  --target langgraph --repeats 2 --seeds 3
```

In CI, dispatch the `live-gauntlet` workflow and escalate `mode` one step at a
time: `smoke` → `probe` → `matrix`.
