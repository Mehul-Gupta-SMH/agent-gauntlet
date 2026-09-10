# agent-gauntlet — Plan

> **Status: design only.** Nothing here is implemented yet. This document is the
> argument for what to build and, just as importantly, what to prove before
> building it. Decisions marked *settled* are settled; everything in
> [Open questions](#open-questions) is not.

## Hypothesis

The best configuration of an agent for a given task cannot be reasoned about in
advance — it has to be *measured*. And the measurement that matters is not
"did it complete the happy path", it is "does it still complete the task when
its tools lie to it".

agent-gauntlet takes a task description plus the access needed to perform it,
generates many candidate agent configurations, runs them against the task under
seeded adversarial conditions, and returns a ranked, cost-aware answer —
together with the winning configuration as a runnable artifact.

**Success criterion (the go/no-go, see [M0](#m0--the-go-no-go-experiment)):**
two independent runs of the same gauntlet, differing only in random seed,
produce leaderboards whose rankings agree. If they do not, the system is an
expensive random number generator and no feature work fixes that.

## The central reframe

The obvious framing is a *tournament*: N agents compete, one wins. That framing
produces a champion but no knowledge, because the variables are confounded — if
variant A (`model X` + `chain-of-thought` + `5 tools`) beats variant B
(`model Y` + `ReAct` + `3 tools`), nothing has been learned about *why*, and
nothing transfers to the next task.

The correct framing is a **factorial experiment over agent configurations**.
Model, prompt strategy, tool set, interaction topology, and sampling parameters
are *factors*. The gauntlet searches that space and reports **per-factor
marginal effects**:

> Prompt strategy is worth 12 points. The model upgrade is worth 4 points at 3×
> the cost. The extra tools did nothing.

That is the deliverable. The champion is a by-product.

## Decisions (settled)

| Decision | Choice |
|---|---|
| Relationship to CommonADK | **Depends on `commonadk` from PyPI.** A variant *is* a generated `common/` folder; materialization onto an SDK is `project.build()`. agent-gauntlet never writes per-SDK agent code |
| What a variant is | A point in a named factor space, serialized as a `common/` project — never an opaque blob |
| Search strategy | **Successive halving** over the factor grid: many configs at low budget, survivors re-run at higher budget. Full factorial only for grids small enough to afford |
| Repeats | **k runs per (variant, scenario)**, always. A single run is not a measurement. Rankings report mean and interval; overlapping intervals mean *no winner declared* |
| Rubric timing | **Pre-registered.** Acceptance criteria and executable checks are generated from the task spec *before any variant runs*, and hashed into the run record |
| Scoring priority | Objective checks first; LLM judging only for what cannot be checked mechanically |
| Judge protocol | Pairwise, position-randomized, ensemble across model *families*; a model never judges its own variant |
| Chaos | First-class and **scored**, not a smoke test. Robustness is its own leaderboard axis |
| Fault determinism | Every fault schedule is **seeded and replayable**. A run that cannot be replayed is not a result |
| Robustness measurement | **Counterfactual pairs** — same variant, same seed, clean vs faulted. Robustness is degradation from a variant's *own* baseline |
| Variant generation | **LLM-generated (an "architect" meta-agent), with a deterministic template generator as fallback** so the whole system runs offline, in CI, with no API keys |
| Generated code execution | **Always sandboxed.** Container isolation, network allowlist, no filesystem outside the workdir, CPU/memory/wall-clock caps. Non-negotiable, and it constrains the architecture, so it is settled up front |
| Leaderboard shape | **Pareto frontier** over quality / cost / tokens / latency / robustness, plus user-supplied weights and "best under budget B" queries. No single hidden scalar |
| Cost accounting | Reports competitor spend and **gauntlet overhead** (architect, critics, judges, chaos) separately. Overhead will often exceed competitor spend |
| Output | The **winning `common/` folder itself** — `pip install commonadk` and run it. A report is not the product |
| Process | Planning lives in `plan.md`; every action is logged in `tasks.md` (inherited from CommonADK's working style) |

## Architecture

Seven subsystems, each independently testable.

```
        task spec + access
                |
                v
   +------------------------+
   |  1. spec               |  task, acceptance criteria, factor space, budget
   +------------------------+
                |
                v
   +------------------------+
   |  2. architect          |  factor point -> a common/ folder
   |     (LLM | template)   |  skill.md, tools.py, agent-config.yaml, MCP wiring
   +------------------------+
                |
                v
   +------------------------+     +------------------------+
   |  3. harness            |<--->|  4. chaos              |
   |     sandbox, runner,   |     |     tool interposer,   |
   |     tool interposer,   |     |     fault schedules,   |
   |     trace + meters     |     |     synthetic data     |
   +------------------------+     +------------------------+
                |
                v
   +------------------------+
   |  5. tribunal           |  executable checks, critics, judge ensemble
   +------------------------+
                |
                v
   +------------------------+
   |  6. ledger             |  immutable run records, replayable
   +------------------------+
                |
                v
   +------------------------+
   |  7. board              |  Pareto frontier, marginal effects, export
   +------------------------+
```

### 1. `spec` — the contract

The user supplies the task, the access it needs (credential *names*, MCP
endpoints, data), and a budget ceiling. The spec compiler produces:

- a **task statement** every variant receives verbatim,
- **acceptance criteria** in natural language,
- **executable checks** — assertions, schema validation, output unit tests,
  trace properties ("must have called `search` before answering"),
- the **factor space** to search,
- **budget caps**, per phase.

Pre-registration is the point. The criteria are hashed into every run record, so
the bar demonstrably was not fitted to whichever output happened to win.

### 2. `architect` — variant generation

Takes a point in factor space and emits a `common/` project. `commonadk
validate` runs on every generated variant *before* it costs a single inference —
malformed generations are rejected free of charge.

The LLM path writes `skill.md` (prompt strategy), `tools.py` (typed functions,
which CommonADK already enforces have hints and docstrings), and MCP server
selection. The deterministic path expands a template matrix over the same factor
space. Both are first-class; the deterministic path is what makes the test suite
runnable offline.

**Generated tools are guilty until proven innocent.** LLM-written tools very
often return plausible hardcoded values — a stub scores beautifully and is
worthless. Before any tool enters a run it must pass static checks (does it
actually make the call it claims?), a smoke test, and stub detection. Tools that
fail are quarantined and the variant is marked.

**Fairness knob:** tool synthesis is itself a factor. When it is *not* the
factor under test, every variant draws from one shared, verified tool pool, so
model and prompt effects are not contaminated by generation luck.

### 3. `harness` — execution and measurement

Builds each variant via `project.build(agent, target=...)`, runs it in the
sandbox, and records:

- the full step trace (messages, tool calls, arguments, results),
- token counts per model call, from provider responses,
- cost, via a versioned price table pinned into the run record,
- latency, split into **model time / tool time / overhead**, measured under
  controlled concurrency — parallel runs distort wall-clock and must not be
  compared against serial ones.

This is the main thing CommonADK does not provide (see
[What CommonADK gives us](#what-commonadk-gives-us)).

### 4. `chaos` — adversity, with an oracle

A **tool interposer** sits between every agent and every tool and MCP call,
injecting seeded deterministic faults.

| Class | Faults |
|---|---|
| **Integrity** | wrong-but-plausible result, stale data, truncation, schema violation, results that contradict each other across calls |
| **Availability** | timeout, 429, exception, empty result |
| **Adversarial** | prompt-injection payload embedded in tool output |
| **Semantics** | silent double-execution of a non-idempotent action |

The wrong-but-plausible case is the headline: the agent believes the call
succeeded and receives a falsehood.

**Why this subsystem has a real oracle.** The harness injected the fault, so it
knows what the true result should have been. "Did the agent silently propagate
the falsehood into its final answer?" is therefore *objectively decidable* — no
judge, no rubric, no bias. Scored behaviours:

- **detection** — did it notice? (cross-check, re-query, sanity assertion)
- **recovery** — did it route around the fault and still complete?
- **honesty** — did it flag uncertainty rather than assert a false result?
- **propagation** — did the falsehood reach the user? (the failure that matters)

Robustness is `clean_score − faulted_score` for the same variant at the same
seed, which controls for variant strength: a weak agent is not credited for
having little to lose.

**Synthetic data** works the same way. The generator emits edge cases per field
domain — nulls, unicode, boundary values, extreme lengths, contradictory
records, adversarial strings, distribution shift — and because it constructed
them, it knows the intended answer for each. Objective grading, again.

### 5. `tribunal` — scoring

Three sources of signal, in descending order of trust:

1. **Executable checks** — deterministic, cheap, unbiased. Maximize their share.
2. **Grounded critique** — variants review each other's work. A critique naming
   a specific defect that a check then *confirms* is strong evidence; ungrounded
   critique is verbosity noise. Critiques are surfaced to the judge as evidence
   and are **never scored directly**.
3. **LLM judging** — for what genuinely cannot be mechanized. Pairwise, position
   randomized, ensembled across model families, self-judging forbidden. The
   known failure modes (self-preference, position bias, verbosity bias) are
   assumed present and designed against rather than hoped away.

**The debate phase is split in two**, because it measures two different things:

- **cold score** — the first, independent attempt,
- **post-critique score** — after seeing peer critique and revising.

Different configurations win each, and both belong on the board. Hard
constraint: the moment a variant sees another's output it is no longer
independent, so debate is a distinct ordered phase with clean state boundaries
and recorded provenance. Known risk from the multi-agent debate literature:
panels converge confidently on wrong answers. Consensus is not evidence.

### 6. `ledger` — reproducibility

A run is an immutable artifact: config + factor point + seeds + pinned model
versions + price table + full trace + scores + environment fingerprint.
Cached tool responses make replay possible without re-spending. A leaderboard
that cannot be regenerated from its ledger is an anecdote.

### 7. `board` — the answer

- **Pareto frontier** across quality, cost, tokens, latency, robustness.
- **Per-factor marginal effects** with intervals — the actual product.
- **Budget queries** — "best config under $2/task", "best under 5s p95".
- **Export** — the winning `common/` folder, ready to run.

## Experiment design notes

- **Fairness invariants.** Identical task statement, scenario set, fault seeds,
  and tool availability for every variant. Scenario order randomized. No variant
  observes another's output before submitting its own. No shared writable state.
- **Cost scales multiplicatively.** N variants × M scenarios × k repeats ×
  (clean + faulted). A naive full grid is trivially 100–1000× the cost of one
  agent run. Successive halving, aggressive caching, cheap-judge-then-escalate,
  and hard per-phase ceilings are load-bearing, not optimizations.
- **Reliability over averages.** Mean score hides an agent that succeeds 6 times
  in 10. Report a pass-rate-at-k style reliability metric alongside quality.

## What CommonADK gives us

| Need | Provided by CommonADK |
|---|---|
| Variant representation | The `common/` folder format; `commonadk validate` rejects malformed generations before they cost inference |
| Model axis | LiteLLM-format strings and alias tables — swapping models is a config edit |
| **Framework axis** | `project.build(agent, target=...)` across six SDKs (Google ADK, OpenAI Agents, Claude Agent SDK, CrewAI, AutoGen, LangGraph) |
| Tool contract | `tools.py` typed functions with enforced hints and docstrings — one uniform seam for the chaos interposer to wrap |
| Topology axis | `interactions.yaml` edges, plus generated mermaid for reporting |
| Credentials | `requires.env` declares names only; build-time preflight checks presence |
| Heterogeneous variants | `build_mixed()` — a variant whose agents sit on *different* SDKs |

The framework axis is the differentiator worth naming: **"the same agent on
CrewAI vs LangGraph vs Google ADK, measured"** is a comparison nothing else
currently runs, and it falls out of CommonADK almost for free.

**The gap.** CommonADK has no execution or telemetry layer — `commonadk run`
executes a single turn. The `harness` is therefore the largest thing to build.
Some of it (observability hooks) is already on CommonADK's own backlog and
should land upstream rather than here.

## Milestones

### M0 — the go/no-go experiment

Before any subsystem is built properly, a deliberately thin vertical slice:

- 1 task, 4 variants (2 models × 2 prompt strategies),
- 3 scenarios × 3 repeats, clean and faulted,
- 2 fault types, executable checks + 1 judge,
- a Pareto board.

Then the only question that matters: **run the whole gauntlet twice under
different seeds and measure rank correlation between the two leaderboards.**

High correlation → the measurement is real, build the rest. Low → stop and fix
measurement (more repeats, more objective checks, better judge protocol) before
adding a single feature. **M0 is a decision gate, not a demo.**

| Milestone | Content |
|---|---|
| **M0** | Vertical slice + the seed-stability gate above |
| **M1** | `spec` and pre-registered rubrics; deterministic `architect`; ledger |
| **M2** | `harness`: sandbox, runner, trace capture, token/cost/latency meters |
| **M3** | `chaos`: interposer, fault taxonomy, counterfactual pairs, robustness scoring |
| **M4** | `tribunal`: executable checks, judge ensemble, bias controls |
| **M5** | LLM `architect` with tool verification and stub detection |
| **M6** | Search: successive halving, budget ceilings, marginal effects |
| **M7** | Debate phase: cold vs post-critique scoring |
| **M8** | `board`: Pareto UI, budget queries, winning-config export |
| **M9** | Synthetic data generation across field domains |

## Open questions

1. **Does the seed-stability gate pass at an affordable k?** If ranking
   stability needs k=20, the economics change completely. Unknown until M0.
2. **How much of "quality" can be made mechanical?** The judge's share of the
   score is the system's noise floor. Task-type-dependent, and worth measuring
   per domain rather than assuming.
3. **Is auto-generated MCP realistic, or is it really selection?** Authoring
   novel MCP servers from scratch is speculative; *discovering and selecting*
   servers from a registry and generating thin wrappers is tractable today. The
   plan assumes selection; revisit if generation proves out.
4. **Does robustness rank differently from quality?** The bet is yes — that the
   happy-path champion is often credulous under fault injection, and that this
   is the finding people will pay for. If the two axes correlate near-perfectly,
   the chaos layer is expensive confirmation and should be demoted.
5. **Does the debate phase earn its cost?** Post-critique gains must exceed the
   inference they consume, or the phase is a feature nobody should run.
6. **How is a self-generated tool that games a check detected?** A variant that
   writes a tool returning exactly what the executable check wants is
   reward hacking. Stub detection is a start; it is probably not sufficient.
7. **Price table drift.** Costs are the most falsifiable number the board
   reports and the fastest to go stale. Pinning it into the run record makes
   old runs honest, not current.

## Prior art

Positioned between four existing traditions, borrowing from each:

| Tradition | Examples | What we take |
|---|---|---|
| Eval harnesses | Inspect, promptfoo, LangSmith, τ-bench, AgentBench, SWE-bench | Scenario/scoring discipline; reliability-at-k over averages |
| AutoML / HPO | Optuna, Hyperband, successive halving | Treating configuration as a search space with a budget |
| Chaos engineering | Chaos Monkey and descendants | Fault injection as routine practice, not incident response |
| Automated agent search | ADAS and similar | LLM-generated candidate architectures |

**What is not already covered by those:** fault injection as a *scored* axis
with an objective oracle, framework portability as a *searchable factor*, and a
**deployable configuration** as the output rather than a number.
