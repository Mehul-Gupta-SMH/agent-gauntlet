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
| `fixtures/lending/applicant.yaml` | `2f32ad9fcecc025b` |
| `fixtures/poisoning/instruction.yaml` | `88c1437a871c5f46` |
| `fixtures/poisoning/memory.yaml` | `8f7d94a1568e6696` |

A failing gate is a **finding**. Record it; do not tune the threshold.

**5. Degrade structurally, never attitudinally.**
You cannot degrade a capable model by asking it to be careless (experiment
003: the prompt-degraded sentinel ranked 6th of 9). The sentinel is a
missing capability. The same doubt applies in reverse — `anchored` is a
prompt and is *not* proven to harden anything.

**6. Secrets are names.** Declared by name, checked for presence. Nothing
reads a value back. `.env` is parsed literally — no expansion, no
substitution.

**6b. There is no sandbox, so reachability is asserted, not inferred.**
An uploaded tool is imported and called *in the server process*, with the
provider keys in its environment. That is allowed only when the operator
passed `--allow-code-execution` **and** the bind is loopback, and any single
request carrying a forwarding header is refused. The bind alone was the old
guard, and a tunnel forwards to loopback — never re-derive permission from a
property the process can observe about itself.

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
- New public behaviour gets a test *and* a note on the issue board.

## Commands

```bash
.venv/bin/python -m pytest -q                       # 352 tests, ~12s
.venv/bin/python -m agent_gauntlet.cli run fixtures/inventory/audited.yaml \
  --repeats 3 --seeds 3 --out /tmp/r                # offline, no spend
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
