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
| `fixtures/inventory/discontinued.yaml` | `18eefc3f8c2d6a0a` |
| `fixtures/lending/applicant.yaml` | `2f32ad9fcecc025b` |
| `fixtures/poisoning/instruction.yaml` | `10347b6eeeba5b0d` |
| `fixtures/poisoning/memory.yaml` | `8f7d94a1568e6696` |

A failing gate is a **finding**. Record it; do not tune the threshold.

A task that writes its own answer into its statement is refused at
generation time (`architect.leaks_answer`): every variant would score
1.00 without calling a tool, and the board would rank a field that never
did the work.

Two hashes have moved, both deliberately and both documented in the
fixture header. `discontinued.yaml` was re-registered the day it was
created, when its hypotheses block was added and no run record existed
against the first hash.

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
.venv/bin/python -m pytest -q                       # 557 tests, ~35s
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
