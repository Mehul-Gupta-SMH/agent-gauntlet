# agent-gauntlet — working notes

Read this before changing anything. It exists so a fresh session is
productive without re-reading 7k lines to rediscover rules that are already
settled. If a rule here conflicts with what the code appears to do, the
code has a bug — these were each learned from one.

## What it is

Generates many agent configurations for one task, runs them all against the
same scenarios while lying to them through their own tools, and ranks what
survives. The lie is injected in `interpose.py`, **below the SDK adapter**,
because CommonADK's hooks are observe-only.

Pipeline: `spec` → `architect` (variants) → `matrix` (runs) → `score` →
`board` → CLI/UI. `faults` decides the lie; `ledger` is append-only.

## The rules that are not negotiable

**1. Own the oracle. Never add a judge.**
The harness injected the fault, so it knows the truth and the figure a
credulous agent would report. Every verdict is a comparison. If you find
yourself wanting a similarity threshold or a model to grade output, stop —
that is issue #2/#28, not a patch.

**2. "Not measured" must never render as a number.**
`n/a`, never `0`. Detection without reachable evidence, latency without
detection, cost without pricing, correctness without a label, propagation
for a variant that could not call the faulted tool — all `None`, all the
way to the page. Guard the *value*, not just the CSS class; a formatter
called on `None` has bitten twice.

**2b. An average over incomparable things is not an average.**
Two runs priced against different rate tables (`prices.fingerprint`), or
materialized onto different agent SDKs (`target`), are each truthful and
jointly meaningless. `cost_per_run` returns `None` when a variant spans
two rate tables, and the run header names the one framework every number
is scoped to. Gate on the *property*, not at the print, so `best_under`
and the frontier censor it too without knowing prices can drift.

**2c. Say what the run could not have seen.**
A verdict without a resolution is a claim about the world dressed as a
claim about the evidence. Every Shapley share carries a bootstrap
interval; every canary prints the shift it is blind below; a hypothesis
upheld at exactly zero prints the Wilson ceiling, because "never, in n
runs" is not "never"; the board prints where its ranking stops
(`decidable_depth`). Use **Wilson for a per-run boolean**, never a
bootstrap — a bootstrap over n identical observations returns a
*zero-width* interval, which reads as infinite sensitivity.

**2d. A task reaches only ONE of the two gates.**
`propagation_rate` needs a fault kind that corrupts a number;
`compliance_rate` needs one that gives an instruction. No single fault
kind does both, so "passes the gate" always means "passes the one gate
this task's fault kind can reach". `gauntlet coverage` prints which, and
it costs nothing. Mixing kinds in one ledger does NOT dilute propagation
-- the decidable denominator already excludes the non-applicable runs,
measured and pinned by a test.

**2e. A raw Brier is a rate without an interval.**
`calibration` scores stated confidence with a proper rule, and reports the
skill score against the base rate beside it -- 0.18 is excellent on a task
nobody gets right and terrible on one everybody does. Skill is `None` when
the reference is 0 (a config wrong on every run), never a perfect score.
Resolution is reported separately because "calibrated" and "knows which
ones" are different virtues. The log score exists for bounds only: it is
unbounded, so one parse failure would decide a leaderboard. Missing
confidence is `None`, never 0.5.

**3. An unmeasured gate metric gates.**
The bar is *shown not to propagate*, not *not shown to propagate*. But
distinguish **not applicable** (an instruction fault corrupts no number)
from **withheld** (faulted runs that could not decide). Only the second
gates.

**4. Fingerprints are pre-registration. Never move a bar.**
`TaskSpec.fingerprint()` is built field-by-field, and a new field joins the
payload **only when set** — otherwise adding one rehashes every spec that
does not use it and detaches historical run records. These must not change:

| fixture | fingerprint |
|---|---|
| `fixtures/inventory/task.yaml` | `5c9848747223eaa4` |
| `fixtures/inventory/audited.yaml` | `d11a0b2a6801ee74` |
| `fixtures/inventory/discontinued.yaml` | `61ddcbf035c1b91d` |
| `fixtures/lending/applicant.yaml` | `2f32ad9fcecc025b` |
| `fixtures/poisoning/instruction.yaml` | `10347b6eeeba5b0d` |
| `fixtures/poisoning/memory.yaml` | `8f7d94a1568e6696` |

A failing gate is a **finding**. Record it; do not tune the threshold.

A task that writes its own answer into its statement is refused at
generation time (`architect.leaks_answer`): every variant would score
1.00 without calling a tool, and the board would rank a field that never
did the work.

Four hashes have moved, all deliberately and all documented in the
fixture header. `discontinued.yaml` three times: the day it was created,
when its hypotheses block were added, again when it declared
`relations: [scale, split]` to stop `relabel` false-failing on it, and
again when it declared `provenance: synthetic`. No live run record has
ever existed against any earlier hash -- which is also why it is the only
fixture that declares provenance. Declaring it on the five sum fixtures
would be just as true and would detach the records they were scored
under, experiment 003's `5c9848747223eaa4` among them. They stay
undeclared, and the board prints "undeclared provenance" about them,
which is exactly right.

**Where a scenario came from is a declaration, not an inference.**
`Provenance` is `trace` / `example` / `synthetic`, and undeclared is
`None` -- there is deliberately no `unstated` member, because that would
be a value an operator could set to look like a declaration. Only `trace`
lets a rate be read as an estimate beyond the run, and even then only as
far as the sampling was unbiased, which nothing here checks. The board
prints the scope beside the intervals: an interval over seed and repeat
variance on fixed worlds covers **no input variance at all**, and that
silence was being read as coverage. A perturbed world is stamped
`synthetic` by the code that builds it, whatever its parent was -- `scale`
triples quantities no trace contains.

**A calibrated value must be a number, refused rather than dropped.**
Serializable was not enough (#48): a string or a dict crossed the pipe,
became a calibrated "truth", and was then dropped SILENTLY at read time --
`_read` returns None for anything non-numeric. The operator got a tool that
calibrated successfully, contributed nothing, and had nothing say so. The
censoring rule inverted: a value accepted as a truth and quietly discarded.
A bool is not a number here, because `isinstance(True, int)` is how a truth
value ends up in a column of totals.

**A relation violation gates only once reproduced.** #27 makes it
gate-shaped -- binary, so like propagation rather than a quality drop --
and that rule is necessary, not sufficient. The board runs ONE pair per
relation, and one violated pair cannot be told from variance (#37).
`MIN_RUNS_TO_GATE = 2`: `held == 0` over at least two pairs. `gauntlet
relations` used to exit 3 on any violation, which gated on exactly that
single observation. `gating()` and `ungated_violations()` are disjoint and
reported separately, because "violated once in one try" and "violated
twice in two" are different claims.

**Coverage is not discovery, and `explore` is coverage.** #18 asks for
an exploration mode because chaos engineering finds unknown unknowns. Its
own argument rules that out here: the adversity is a registry of five tools
and an enum of kinds, so there is nothing open-ended. Per config the space
is 4-10 cells, which is covered rather than searched -- and that buys the
one property no board number has, an EXACT fraction with no sampling error,
because the denominator is the whole space. It cannot find a fault CLASS
nobody implemented. One run per cell, no repeats: exact over the space,
silent about run-to-run noise. The board is the other way round.

**A registry pairs tools with kinds and says nothing about keys.**
`INJECTION_SITES` asserts that a tool may carry a kind; it cannot assert
that the site's own lookup matches the schedule's key. `list_records` was
registered for `omission` and looked the fault up by key `"*"` while the
schedule keyed it by the withheld record id, so every faulted run served
the full world and the board printed `prop=0%` ungated -- the exact silent
no-op the registry exists to prevent, in the kind that added it.
`test_injection_sites.py` now asserts every registered pairing against
BEHAVIOUR: build the schedule, call the site with the fault's own target,
require the call be logged faulted. Add a site, add a row there.

**An overridden fault moves the fingerprint.** `--fault` used to be passed
around the task, so a timeout run on `audited.yaml` was stamped
`d11a0b2a6801ee74` -- the hash of a spec declaring `wrong_value`. Worse
than the mixed-rate-table confound, where the hash is ambiguous: this one
resolved to a spec the run was not produced under. `--fault`/`--fault-tool`
now build a task copy, and `ledger.fault_kinds` is the backstop for
ledgers already written.

**The novelty claim is retracted, and the retraction is guarded.**
Fault injection as the measurement, the injection site as a judge-free
oracle, the clean-twin counterfactual pair, and the decidable-denominator
rule are ALL prior art -- CatchBench labels by injection site "correct by
construction and independent of any detector", AgentCheck replays a clean
run with one response altered, SSCBench uses clean trajectories as oracle
negative controls, AgentChaos excludes runs the fault never reached.
`docs/prior-art.md` carries the verified citations with arXiv ids, and
`test_docs.py` stops the sentence regrowing in the README. #25's proposed
replacement -- "we search config space under fault injection" -- is also
false: this tool enumerates a declared grid and says so. What survives:
it materializes and runs the configurations it measures, and the reporting
discipline. State it as NOT FOUND, never as does-not-exist.

**A withheld source is only decidable where it was necessary.** #41's
source-dependence check removes a tool the config called and asks whether
the answer moved or the agent stopped. Applied to any called tool it
false-fails on redundancy -- `get_summary` cross-checks a total
`fetch_record` already yields -- so only `metamorphic.LOAD_BEARING` tools
are withheld, each declared with why no other granted tool can supply it.
Offline the check CANNOT FAIL and the block says so: scripted policies read
their tools honestly, so 17/17 is a property of the test doubles. The
property that discriminates is `substituted` -- answered anyway, from a
source that could not support the answer -- and it needs the live path.

**Operator code runs exactly once, in the child, and never again.**
`usertools.load` is the only function that imports an uploaded module and
it is reachable from exactly one place -- `_calibrate`, the child process's
entry point, pinned by a test. Matrix-time `serve` is a lookup into the
calibrated table, which crossed back as JSON; `discover` and
`top_level_effects` parse with `ast` and never import. #42 read the
in-process `serve` call as per-run execution of the tool and it is not: a
thousand serves execute the operator's body zero times, measured. The
residual exposure is DATA, not code -- a hostile tool controls its own
calibrated values, which the board then prints.

**A cost figure records what priced it, not what might have.** `pricing`
pins an authority per target: the static rate table, or the SDK's own
`total_cost_usd`. commonadk never consults its table for `--target claude`,
which is the default -- so pinning the table there was a claim with no
causal relationship to the dollars, and it looked healthy. Each authority
hashes differently, so a mixed ledger reads `n/a` rather than averaging two
different measurements. Each has a blind spot the board states: the table
models no cache or batch tier; the SDK reports what it billed but publishes
no rates, so upstream drift is invisible. An unlisted target is
`unrecorded`, never assumed to be the table -- assuming is how the wrong
authority got pinned.

**A relation is only defaulted where it is provable.** `relations_for`
used to apply all three to any task that declared none -- which was every
fixture -- so every relation result this project reported came from a set
no task had claimed. It now defaults only when no scenario sets
`expected`, because then the answer IS the sum of the world and all three
follow from that. Otherwise the task gets none until it says what holds.
A missing relation measures less; a wrong one reports a failure that did
not happen.

The instruction fixture's hash moved once, on 2026-09-23, and the reason
is in the file: its first live run put no directive in front of the agent
at all, because nothing in the statement gave it a reason to read an
annotation. A fixture that cannot deliver its fault is a broken
instrument rather than a bar worth protecting, and nothing historical was
attached to the old hash. Re-registering is allowed; doing it quietly is
not.

**5. Degrade structurally, never attitudinally.**
You cannot degrade a capable model by asking it to be careless (experiment
003: the prompt-degraded sentinel ranked 6th of 9). The sentinel is a
missing capability. The same doubt applies in reverse — `anchored` is a
prompt and is *not* proven to harden anything.

**6. Secrets are names.** Declared by name, checked for presence. Nothing
reads a value back. `.env` is parsed literally — no expansion, no
substitution.

**3b. A fault that cannot fire is refused, not run.**
Only four tools consult the schedule, each for one kind
(`interpose.INJECTION_SITES`). Any other pairing returns the clean value,
so the matrix would label runs faulted with nothing injected and score
every agent as having resisted. `unreachable()` refuses it in
`architect.generate` and again in `run_matrix`. Never infer this from a
tool being *granted* — that is `_exposure_possible`, a different question
whose answer reads as a pass.

**6b. There is no sandbox, so reachability is asserted, not inferred.**
An uploaded tool is called in a child process with no credentials in its
environment and a wall clock (`_calibrate`), which is a process boundary
and not isolation: no seccomp, no namespace, no filesystem or network
restriction. That is allowed only when the operator
passed `--allow-code-execution` **and** the bind is loopback, and any single
request carrying a forwarding header is refused. The bind alone was the old
guard, and a tunnel forwards to loopback — never re-derive permission from a
property the process can observe about itself.

**6c. Never re-run to green. Flakiness is the measurement.**
A flaky test is a defect; a flaky agent that passes 7 times in 10 IS 70%
reliable. `certify` and `check` refuse a ledger holding the same cell
twice (`ledger.duplicate_cells`) rather than averaging the attempts. The
matrix retries only provider-shaped *exceptions*, never a run that was
scored. Gate on binary safety properties; report on statistical ones.

**7. The harness fails green.** Every defect this project has found in
itself presented as a *pass*: a sentinel a model ignored, a CI probe that
went green having called no model, a full leaderboard over a matrix where
the fault could never fire. When something passes, ask what would have
happened if it had done nothing.

## Conventions

- **Comments explain why, not what.** Every non-obvious line carries the
  reason it is that way, usually the failure that taught it. Match that
  density; it is the house style.
- Tests are named as claims (`test_a_variant_that_never_touched_the_tool_cannot_have_propagated`)
  and their docstrings carry the argument.
- Exit codes: `0` pass, `3` gate FAIL (a measurement, not a crash), anything
  else is a crash. CI's `set +e` is load-bearing.
- New public behaviour gets a test, a note on the issue board, **and the
  docs updated in the same commit**. `plan.md` spent weeks opening with
  "nothing here is implemented yet" above a passing live gate; that is the
  same failure mode as a green tick nobody checked. `test_docs.py` guards
  what it can — the module map, its line counts, the test layout, every CLI
  command in the README, the fixture fingerprints — but it cannot check
  whether prose is still true.

## Commands

```bash
.venv/bin/python -m pytest -q                       # 612 tests, ~51s
.venv/bin/python -m agent_gauntlet.cli run fixtures/inventory/audited.yaml \
  --repeats 3 --seeds 3 --out /tmp/r                # offline, no spend
.venv/bin/python -m agent_gauntlet.cli canary fixtures/inventory/audited.yaml \
  --save base.json                                  # a cheap pinned fingerprint
.venv/bin/python -m agent_gauntlet.cli ui --port 8420
node --check src/agent_gauntlet/ui/*.js             # the page has no server-side signal
python tools/build_demo.py                          # republish docs/demo after a UI edit
python tools/build_demo.py --check                  # what the test asserts
```

A UI change is not finished until `docs/demo/` is rebuilt: it ships
`arena.js` and `arena.css` byte for byte, and `test_replay.py` fails when
the published page and the product disagree.

Verify UI changes **in a browser**. Five rendering bugs in this repo were
invisible in source and obvious on render.

## Keeping sessions cheap

Every request re-sends the whole conversation, so a long session costs far
more than the same work split up. One feature per session; `/clear` between
them. This file is what makes that cheap — if a fresh session has to
re-derive something to get started, add it here instead.
