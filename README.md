# agent-gauntlet

**Which agent configuration should you actually ship?**

agent-gauntlet takes a task, generates many candidate agent configurations —
different models, prompts, tools, frameworks — runs them against that task while
**deliberately lying to them through their own tools**, and returns a ranked,
cost-aware answer plus the winning configuration as something you can run.

> **Status: early build.** The loop runs end to end — `gauntlet run` generates
> variant `common/` projects, runs them under seeded fault injection, scores
> mechanically, and exports the winner as a runnable project. The live path is
> verified against a real model. The measurement gate that decides whether any
> of this is worth building ([M0](plan.md#m0--the-measurement-gate)) has **not**
> been answered — its bar is pre-registered and the matrix has not yet run.
> Architecture and open questions in [`plan.md`](plan.md); every result so far
> in [`experiments/`](experiments/).

## The idea in one loop

```
task + access
     |
     v
generate variants        many configurations, each a runnable agent project
     |
     v
compete under chaos      same task, same scenarios, seeded tool faults
     |
     v
critique + judge         executable checks first, LLM judging only where needed
     |
     v
leaderboard              quality / cost / tokens / latency / robustness
     |
     v
export the winner        a config you can deploy, not a PDF
```

## Three things that make it different

**1. Chaos is scored, not smoke-tested.** A tool interposer sits between every
agent and every tool call, injecting seeded faults — timeouts, schema
violations, prompt-injection payloads, and above all *wrong-but-plausible
results*, where the agent believes the call succeeded and receives a falsehood.

Because the harness *injected* the fault, it knows the truth. So
"did the agent silently propagate the falsehood into its final answer?" is
objectively decidable — no rubric, no judge, no bias. That is a rare thing in
agent evaluation, and it is the core of the project.

**2. The framework is a searchable variable.** Built on
[CommonADK](https://github.com/Mehul-Gupta-SMH/CommonADK), which materializes
one framework-neutral project definition onto six agent SDKs (Google ADK, OpenAI
Agents, Claude Agent SDK, CrewAI, AutoGen, LangGraph). So "the same agent on
CrewAI vs LangGraph vs Google ADK, measured" is just another axis of the search.

**3. It answers with knowledge, not a champion.** A tournament tells you variant
#7 won and nothing else — the variables were confounded. agent-gauntlet treats
model, prompt strategy, tool set, and topology as named factors and reports
**per-factor marginal effects**:

> Prompt strategy is worth 12 points. The model upgrade is worth 4 points at 3×
> the cost. The extra tools did nothing.

## Results so far

Raw output for everything below is committed in [`experiments/`](experiments/).
Numbers quoted here are traceable to a file there.

### The harness measures what it claims

[Experiment 001](experiments/001-offline-harness-validation/) — 324 offline
runs, $0.00. Scoring classifies all four terminal outcomes, propagation is
confined to credulous variants and gates them, the sentinel ranks last, and the
pre-registered gate renders a verdict.

It also **fails its own gate**, correctly:

```
median tau      = 1.0
top-1 stability = 0%
GATE: FAIL -- top-1 stability 0.00 < 0.6
```

Perfect rank stability *and* no unique winner. Read alone, tau 1.0 looks like a
resounding pass; it is vacuous, because a metric that cannot separate variants
cannot be unstable. The gate refusing to certify that is the point.

### The live path works

[Experiment 002](experiments/002-live-path-validation/) — ~$0.05 across four CI
dispatches. A generated project loaded under CommonADK, the langgraph runner
drove `claude-sonnet-5`, tools were called through the interposer, and the
answer parsed and scored. Token counts and `cost_usd` arrive complete.

Getting there cost two defects, each of which would have produced a full matrix
of confident, believable zeros:

1. The trace reader probed for attribute names that do not exist on CommonADK's
   events.
2. `RunFinished.final_text` carried `repr()` of the model's content-block list,
   so no line-anchored pattern could match it.

**The second one matters most.** The harness's own diagnostics concluded *"the
model produced text but not in the required format."* That was false — the
model had followed the contract exactly and computed the right answer. A false
finding about instruction-following is precisely the error this project exists
to avoid making about other people's agents, and the harness made it about its
own. It is now a regression test built from the captured reply.

### What has not been measured

Whether a ranking of agent configurations survives a change of random seed —
the question the project exists to answer. The bar was
[pre-registered first](fixtures/inventory/task.yaml) (median Kendall's tau ≥
0.7, top-1 stability ≥ 0.6, seeds ≥ 3) and is covered by the task fingerprint,
so it cannot be moved afterwards without changing the hash every run record
carries.

## Honest caveats, up front

- **Scoring is the whole ballgame.** Everything downstream is only as good as
  the judge. The design pushes as much scoring as possible onto executable
  checks and treats LLM-as-judge bias as a certainty to be engineered against.
- **One run is not a measurement.** Agent runs are high-variance. Every variant
  runs k times per scenario, and no winner is declared when intervals overlap.
- **This is expensive.** Variants × scenarios × repeats × (clean + faulted) is
  easily 100–1000× the cost of a single agent run. Budget ceilings and
  successive halving are load-bearing parts of the design, not optimizations.
- **Generated code runs sandboxed. Always.** LLM-written tools executing against
  real credentials is the obvious way for a project like this to hurt someone.
- **The harness is a suspect too.** Every defect found so far produced a
  plausible-looking board rather than an error, and one of them blamed the model
  for a harness bug. Cheap checks that escalate — `smoke`, then `probe`, then
  the matrix — exist because a confident wrong number costs more than a crash.

## Before any of it gets built

There is a single go/no-go experiment: run the whole gauntlet twice, changing
only the random seed, and measure whether the two leaderboards agree.

If they do not, this is an expensive random number generator and no amount of
feature work fixes that. See [`plan.md`](plan.md#m0--the-measurement-gate).

## Related

- [CommonADK](https://github.com/Mehul-Gupta-SMH/CommonADK) — define an agent
  system once, build it on any agent SDK. agent-gauntlet is built on it.

## License

MIT. See [LICENSE](LICENSE).
