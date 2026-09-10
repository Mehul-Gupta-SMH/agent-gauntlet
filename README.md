# agent-gauntlet

**Which agent configuration should you actually ship?**

agent-gauntlet takes a task, generates many candidate agent configurations —
different models, prompts, tools, frameworks — runs them against that task while
**deliberately lying to them through their own tools**, and returns a ranked,
cost-aware answer plus the winning configuration as something you can run.

> **Status: design stage.** No implementation yet. The architecture, the open
> questions, and the experiment that decides whether this is worth building at
> all live in [`plan.md`](plan.md). Feedback on the design is the most useful
> contribution right now.

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

## Before any of it gets built

There is a single go/no-go experiment: run the whole gauntlet twice, changing
only the random seed, and measure whether the two leaderboards agree.

If they do not, this is an expensive random number generator and no amount of
feature work fixes that. See [`plan.md`](plan.md#m0--the-go-no-go-experiment).

## Related

- [CommonADK](https://github.com/Mehul-Gupta-SMH/CommonADK) — define an agent
  system once, build it on any agent SDK. agent-gauntlet is built on it.

## License

MIT. See [LICENSE](LICENSE).
