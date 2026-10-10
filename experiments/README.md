# Experiments

Every run that produced a result, with its raw output committed alongside the
writeup. A number quoted in the README or an issue should be traceable to a
file in here.

Three rules this directory follows:

- **Captured, not transcribed.** `console.txt` and `summary.json` are the
  actual artifacts. Prose in a writeup can be wrong; the artifact next to it
  cannot be quietly edited to agree with it.
- **Negative results are results.** An experiment that failed its own gate, or
  that found the harness at fault, stays in the record at the same weight as
  one that passed.
- **A live experiment commits its ledger.** `runs.jsonl` goes in the directory,
  not just the printed board. Experiments 003 and 007 broke this rule before it
  was written down: roughly $10 of real runs whose per-run records live only in
  an Actions artifact, so [experiment 010](010-robustness-vs-quality/) had to
  answer its question by *parsing the printed table*. Artifacts expire; a
  committed file does not. `tools/rescue_ledger.sh` files one, and 003 and 007
  are still outstanding — their artifacts expire **2026-12-14** and
  **2026-12-15** (#46).

| # | Experiment | Cost | Outcome |
|---|---|---|---|
| [001](001-offline-harness-validation/) | Offline harness validation | $0.00 | Machinery correct; **gate FAILs** on a tied ranking, which is the right verdict |
| [002](002-live-path-validation/) | Live path validation | ~$0.05 | Path works end to end; found two defects that would each have voided a matrix |
| [003](003-live-m0-gate/) | The M0 gate, live | ~$5 | **Gate FAILs**, and so does the instrument check — the sentinel ranked 6th of 9 |
| [004](004-hardened-fixture-probe/) | Does the hardened fixture work live? | $0.02 | **PASS** — the reconciliation lands on a real model; found a probe that went green having called nothing |
| [005](005-fifth-outcome-live/) | The fifth outcome, caught in the wild | $0.02 | `SURFACED_BUT_PROPAGATED` fired on its first live faulted run — the taxonomy gap was real |
| [006](006-live-instrument-check/) | The instrument check, against a real model | $0.03 | **PASS** — the structural sentinel undercounts by 47% live; the check printed a false number about itself |
| [007](007-m0-gate-passed/) | **The M0 gate** | ~$5 | **PASS** — tau 0.845, top-1 100%, sentinel last. #1 answered. Found that the run recorded no cost |
| [008](008-catching-a-regression/) | Does the check catch a regression? | $0.00 | **CAUGHT** — a prompt edit moved propagation 0% → 100% and the certificate refused it |
| [009](009-how-many-repeats/) | How many repeats does a stable board need? | $0.00 | **MEASURED** — k=4 on this fixture; `--repeats 3` was one short, and depth 3 never settles at any k |
| [010](010-robustness-vs-quality/) | Does robustness rank differently from quality? | $0.00 | **BET HOLDS** — across both live matrices the clean ranking is an 8-way tie, so tau is undefined: the faulted half is the only axis that ranked anything |
| [011](011-is-a-timeout-as-informative/) | Is a timeout as informative as a lie? | $0.00 | **MEASURED** — identical on correctness to 3 decimals, incomparable on safety; found two outcomes whose names were not true |
| [013](013-the-probe-was-an-experiment/) | The probe was an experiment nobody was recording | $0.00 | **FOUND** — twelve paid-for live runs that disagreed with each other, read out of job logs by hand; the same cell both propagates and repairs |
| [014](014-the-band-swallowed-the-verdict/) | The band swallowed the verdict | $0.05+ | **FOUND** — two live runs reported the credulous figure to the unit and scored "no falsehood propagated"; the corruption was below the separability threshold, and nothing said so before the money was spent. The cost is a floor: see 015 |
| [015](015-what-the-flat-table-misses/) | What the flat table misses, and in which direction | $0.00 | **MEASURED** — #47's ratio, from probes already paid for: the flat table underprices by 1.83×–2.29×, the opposite direction from the premise. Also found that every live row's cost is the clean half's while its outcome is the faulted half's |

## Where that leaves the question

*Does a ranking of agent configurations survive a change of random seed?* (#1)
is **answered, for this task.** [Experiment 007](007-m0-gate-passed/): median
tau 0.845 with top-1 stability 100% across three seeds, on a board whose
sentinel ranked last by 35 accuracy points.

The pair of numbers is the point. Experiment 003 also produced a tau the gate
would have liked — 1.0 — and it was vacuous, because every variant tied and a
metric that cannot separate variants cannot be unstable. Here the ranking
genuinely moves between seeds (tau < 1) and every seed pair still agrees on
the winner. Discrimination and stability together.

What it does not cover: one task, one framework, k=2, and a field of one
candidate after gating. Scope is spelled out in the writeup.

[Experiment 009](009-how-many-repeats/) then measured the k that 007 had
assumed. On this fixture the board stops moving with the seed at **k=4**, one
above the `--repeats 3` every M0 run has used — and depth 3 of the ranking
never stops moving at any budget, because the configurations there are tied in
truth. So the answer has a shape 007 could not see: stable at the top, and
never stable below it. It is offline, against scripted policies at a declared
slip rate, so it measures the machinery rather than real agents (#33).

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
| `probe` (`live-probe.yml`) | ~$0.03 | the live path clean **and** faulted, plus the instrument check | **automatically**, on every push to `main` that touches code, plus daily |
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

Its third call is the **instrument check** (#29), and it is there because of
how experiment 003 failed. That sentinel was degraded by a prompt: offline a
scripted policy had no choice but to comply, so it ranked last and the
instrument looked sound; live, a capable model ignored the instruction and
ranked it 6th of 9, voiding the whole board including its gate verdict. The
structural sentinel that replaced it had, until now, only ever been validated
offline — in the one environment that cannot falsify it.

So the probe runs the sentinel against a real model and asserts it does **not**
get the right answer. If a model recovers the truth from a truncated record
list, the degradation was never structural and a matrix is void before it
starts. That is a hard failure, for the price of one call rather than a whole
matrix.

The probe never fails the build for something a contributor cannot fix. It
exits 4 when the model is unreachable (outage, rate limit, revoked key) and CI
turns that into a warning; a missing secret skips the job entirely. Only a
genuinely broken path goes red. That distinction is the whole reason the probe
can be automatic at all — `ci.yml` spent several days red because a
pre-registered gate FAIL, which is a measurement, was being read as a build
failure.

## Committing the ledger (#46)

**Every paid matrix commits its own `runs.jsonl`.** `live-gauntlet.yml` does
it, to `experiments/live/<run id>/`, with a generated README naming the
fixture, target, seeds, repeats, commit and run URL.

This reverses what this section said first. I had argued for a documented
manual step on the grounds that `contents: write` is a wider change than it
looks — the operator is already touching the directory when they write the
experiment up, so why give a workflow push rights for a few hundred KB. The
counter-argument won on the evidence: a manual step is exactly what was in
place for 003 and 007, and both ledgers are **still** only in Actions
artifacts, now expiring. A step that depends on someone remembering it, when
forgetting is silent and the cost lands 90 days later, is not a step.

The scope is the mitigation rather than the trust: the commit step writes only
under `experiments/live/<run id>/`, runs only for `mode == matrix`, commits
only `runs.jsonl`, `summary.json` and its own README, and rebases rather than
forces — a human may well have pushed during a 45-minute matrix. It never
touches a fixture, a workflow, or anything under `src/`.

The offline experiment got this right first — [008](008-catching-a-regression/)
commits `before.jsonl` and `after.jsonl` — and the expensive live ones did not.
[Experiment 010](010-robustness-vs-quality/) had to answer #4 by parsing the
printed board table out of `console.txt`, because the per-run records were
gone. That worked and it capped the analysis at whatever the board happened to
print in September: no outcome mix per variant, no per-scenario breakdown, no
detection-latency distribution, no clean/faulted pairing at run granularity.

A live matrix costs ~$5 and half an hour. The ledger is a few hundred KB.
Actions artifacts expire on the retention setting (90 days by default), so
"it's in the artifact" is a deadline, not a location.

### What a ledger carries, checked rather than assumed

> A spec must never carry a secret, and the same care applies to a ledger.

Checked. A `RunRecord` holds **no model output text at all**: `answer` is an
`Answer`, whose only fields are `total`, `flagged_anomaly` and `confidence` —
a number, a bool and a number. There is no transcript, no completion text, no
prompt.

The only free text a record can carry is:

* **`tool_calls[].result`** — whatever the tool returned. For `read_annotation`
  that is the **injected directive**, which is the harness's own text from
  `faults.DIRECTIVES` rather than anything of the operator's.
* **`error`** — an exception message from a failed run. This is the one field
  worth reading before committing, because its content comes from a provider
  rather than from here. The workflows never put a key where an exception
  could echo it (they print `ANTHROPIC_API_KEY`'s *length*, never its value),
  but "a provider error string is not ours to vouch for" is the right posture.

So the workflow **prints every `error` string it is about to commit**, with a
count, and leaves the judgement to whoever reads the run:

```
--- error strings in this ledger, review before trusting it ---
  provider said: 529 overloaded_error
  (1 of 180 record(s) carried an error)
```

Automating the commit and automating the *vouching* are different things, and
only the first is safe to do without a human.

### Outstanding

The 003 and 007 ledgers are still only in Actions artifacts, and this cloud
session cannot fetch them — `gh` refuses Actions artifact downloads by design,
not for want of a flag. `tools/rescue_ledger.sh` makes it one command to run
locally. **The artifacts expire around mid-December 2026.**

**One thing here *is* reachable, and it took two blank rows to notice.** A
workflow's own `::warning::` and `::error::` lines become **check-run
annotations**, and those are served by `api.github.com` — no artifact host, no
log download. `repos/{owner}/{repo}/commits/{sha}/check-runs` then
`repos/{owner}/{repo}/check-runs/{id}/annotations` reads them. Two probes on
`poisoning/memory.yaml` recorded a clean half, an injection, a cost and no
outcome, and the reason existed in exactly one place: an annotation saying
`probe failed (exit 1) on the rotated cell`. That rules out the two other
causes the row was compatible with.

This does not rescue the ledgers — a ledger is a file in an artifact, not an
annotation. It does mean a workflow that *says* what it concluded leaves a
readable record even when its output does not survive, which is worth more than
it sounds: the probe now records `probe_exit` directly, so the next blank row
explains itself without the crawl.

## The probe records itself too (#46, experiment 013)

`live-probe.yml` appends one line per run to `experiments/live/probe.jsonl`,
parsed from the console by `tools/probe_record.py`.

This was not in the original rule and it should have been. Twelve probes ran on
2026-10-09 — one per push to `main` — all green, all paid for, and **none of
them readable in aggregate**: the artifacts expire and the job logs go with
them. Four were eventually read out of the logs by hand, and they disagreed
with each other. See [013](013-the-probe-was-an-experiment/).

The matrix was the obvious case for committing evidence because it is
expensive. The probe was the case that mattered, because it runs constantly and
nobody was looking.

Parsing a console is a poor interface; a `--json` flag on `gauntlet probe` would
be better. The recorder exists in this shape because the console format is what
twelve already-paid-for runs emitted, and a recorder that only works after a
refactor records nothing today.
