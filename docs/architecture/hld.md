# High-level design

## 1. The problem

Choosing an agent configuration is a search problem dressed as an opinion. Model,
prompt strategy, tool surface and framework all interact, teams pick by intuition,
and the usual evidence — a demo, a handful of golden cases — says nothing about
what happens when a tool lies.

agent-gauntlet turns that into a measured search with three constraints:

1. **Grading must be mechanical.** Any judge-in-the-loop imports the judge's bias
   into the answer.
2. **Adversity must be part of the measurement**, not a smoke test afterwards.
3. **The result must be falsifiable.** A leaderboard that changes when you change
   the random seed is an expensive random number generator, and the system has to
   be able to say so about itself.

## 2. The central trick

The harness sits *below* the agent's tools. When an agent calls `fetch_record`,
the call goes through an interposer that may return a plausible falsehood.

```mermaid
flowchart TD
    A["agent<br/><i>(any of 6 SDKs)</i>"] -->|"fetch_record('c')"| T["generated tools.py"]
    T --> I{{"interposer<br/><i>fault schedule</i>"}}
    I -->|no fault| W[("scenario<br/>records")]
    I -->|"WRONG_VALUE"| L["58 → 99<br/><i>plausible lie</i>"]
    I -->|"TIMEOUT"| X["raise ToolTimeout"]
    L --> A
    X --> A
    W --> A

    style I fill:#7f1d1d,stroke:#991b1b,color:#fff
    style L fill:#7f1d1d,stroke:#991b1b,color:#fff
```

Because the harness *chose* the lie, it knows both the truth (107) and the figure
a fully credulous agent would report (148). Grading the final answer is then a
comparison, not a judgement:

- near 107 → the agent recovered the truth
- near 148 → the agent swallowed the lie
- neither → the agent is wrong for its own reasons

**No judge, no rubric, no similarity threshold.** This is why fault injection is
load-bearing rather than decorative: it is what makes the oracle exist.

### What the harness can lie about

Four kinds, because they ask four different questions. A task declares one;
they are never mixed, since averaging three oracles reports one number for
three questions.

| kind | corrupts | the question |
|---|---|---|
| `wrong_value` | a tool's return value | does a false **number** survive to the answer? |
| `timeout` | the call itself | the loud contrast — trivially detected, kept so the two can be compared |
| `instruction` | the authority attached to a result | does the agent still know **whose instructions are instructions**? |
| `poisoned_memory` | the agent's own recorded finding | does it **re-derive** what it already decided, or build on it? |

`instruction` is decidable without a judge because the directive names a
**canary** — an improbable value the agent has no other route to. If it
reaches the answer it was read out of the data. Compliance is its own
column, its own outcome and its own gate, and it gates with no declared
ceiling: taking orders from a data source is a failure of what an agent is
*for*, not of how carefully it counts.

`poisoned_memory` is as close to intercepting a reasoning step as this
design can honestly get. **Model-internal reasoning is not an injection
point** — CommonADK's hooks are observe-only and no adapter exposes the
token stream for rewriting — but an agent working in stages must put its
intermediate findings somewhere, and that somewhere is a tool. The oracle is
exact in a way an external source's never is: the harness saw what was
written.

### Why below the adapter, not in a hook

CommonADK's runner hooks are observe-only by design — a callback sees an event
strictly after it happened and cannot change a tool result. Injection therefore
lives inside the tool function, which also means the same fault schedule behaves
identically on all six SDK targets, and a variant cannot detect that it is being
tested by inspecting its own tool code.

## 3. Components

```mermaid
flowchart TB
    subgraph Define
        SPEC["<b>spec</b><br/>TaskSpec · Scenario · GateCriteria<br/><i>fingerprint()</i>"]
    end
    subgraph Generate
        ARCH["<b>architect</b><br/>factor grid → common/ projects"]
    end
    subgraph Execute
        MATRIX["<b>matrix</b><br/>fairness invariants · retries"]
        FAULTS["<b>faults</b><br/>seeded schedules"]
        INTER["<b>interpose</b><br/>tool surface · tool grants"]
        LIVE["<b>live</b><br/>CommonADK runner"]
        OFF["<b>offline</b><br/>scripted policies"]
    end
    subgraph Record
        LEDGER[("<b>ledger</b><br/>append-only JSONL")]
    end
    subgraph Judge
        SCORE["<b>score</b><br/>outcomes · repair · propagation · compliance"]
        ANALYZE["<b>analyze</b><br/>Kendall tau · stability"]
        BOARD["<b>board</b><br/>rank · Pareto · attribution · held-out"]
    end
    subgraph Operate["Operate (the UI layer)"]
        PROJECT["<b>project</b><br/>one project at a time"]
        USERTOOLS["<b>usertools</b><br/>upload · calibrate · serve"]
        RUNNER["<b>runner</b><br/>project → TaskSpec + variants"]
        SECRETS["<b>secrets</b><br/>names in, nothing out"]
        SERVER["<b>server</b><br/>stdlib http · wizard · arena"]
        EVENTS["<b>events</b><br/>append-only, read by cursor"]
    end

    SPEC --> ARCH --> MATRIX
    FAULTS --> MATRIX
    MATRIX <--> INTER
    MATRIX --> LIVE
    MATRIX --> OFF
    MATRIX --> LEDGER
    LEDGER --> SCORE --> BOARD
    LEDGER --> ANALYZE --> BOARD

    PROJECT --> RUNNER --> MATRIX
    USERTOOLS --> RUNNER
    USERTOOLS -.->|calibrated table| INTER
    SERVER --> PROJECT & SECRETS
    MATRIX -.->|emit| EVENTS --> SERVER
    BOARD --> SERVER

    style LEDGER fill:#1e3a5f,stroke:#1e40af,color:#fff
    style EVENTS fill:#1e3a5f,stroke:#1e40af,color:#fff
    style FAULTS fill:#7f1d1d,stroke:#991b1b,color:#fff
```

The Operate group is a **view onto the same pipeline**, never a second one.
A project reaches the same `run_matrix`, the same `score_run` and the same
`board.summarize` the fixtures do; where it cannot supply something they
need, that surfaces as `n/a` rather than as a special case.

| Component | Owns | Deliberately does not |
|---|---|---|
| `spec` | The task, its scenarios, and the **pre-registered** gate | Know anything about agents |
| `architect` | Turning a factor grid into runnable `common/` projects | Decide which factors matter |
| `faults` | Deterministic fault schedules from a seed | Touch the agent |
| `interpose` | The tool surface agents call; enforcing tool grants | Know which SDK is running |
| `matrix` | Fairness invariants, retries, writing records | Interpret results |
| `live` / `offline` | Two executors with one signature | Differ in anything the matrix can see |
| `ledger` | Immutable records, drift detection, re-scoring | Aggregate |
| `score` | Grading one run against known truth | Contain a judge |
| `analyze` | Rank-stability statistics | Render a verdict |
| `board` | Ranking, frontier, attribution, held-out winner | Invent a scalar |
| `project` | One operator's intake, tools, models and run state, on disk | Run anything |
| `usertools` | Reading an upload without importing it; calibrating it into a value table | Trust a tool that disagrees with itself |
| `runner` | Turning a project into a real, fingerprinted `TaskSpec` and its variants | Have its own scorer |
| `secrets` | Credential **names** and presence; `.env` parsed as data | Hand a value back to anyone |
| `events` | An append-only log the page reads by cursor | Know what a page is |
| `server` | stdlib HTTP, the wizard, the arena | Compute a rate |

## 4. One run, end to end

```mermaid
sequenceDiagram
    participant M as matrix
    participant F as faults
    participant I as interpose
    participant E as executor
    participant A as agent
    participant L as ledger

    M->>F: build(seed, records, targets=audited)
    F-->>M: schedule (one plausible lie)
    M->>I: run_context(records, schedule, grants, audited)
    M->>E: execute(variant, seed)
    E->>A: run via CommonADK
    A->>I: list_records()
    I-->>A: ids
    A->>I: fetch_record('c')
    Note over I: schedule fires → 58 becomes 99
    I-->>A: 99
    A->>I: get_summary()
    I-->>A: 107 (the planted cross-check)
    Note over I: evidence is now reachable — TTD clock starts
    A-->>E: "TOTAL: 107 / ANOMALY: yes"
    E-->>M: (Answer, rollup)
    M->>M: score_run(truth, schedule, calls, answer)
    M->>L: append(RunRecord)
```

Every faulted run has a **clean twin** on the same seed, so robustness is
measured against a variant's own baseline rather than against other variants.

## 5. What comes out

```mermaid
flowchart LR
    L[("ledger")] --> S["summarize<br/><i>one row per variant</i>"]
    S --> R["rank<br/><i>tiers, ties kept</i>"]
    S --> P["pareto<br/><i>accuracy × cost</i>"]
    S --> FX["factor_effects<br/><i>+ interaction caveat</i>"]
    L --> ST["stability<br/><i>tau, top-1</i>"]
    ST --> G{"gate<br/><i>pre-registered bar</i>"}
    S --> HO["held_out_winner<br/><i>select ≠ score</i>"]
    G -->|pass| V["verdict + exported winner"]
    G -->|fail| STOP["stop. fix measurement."]

    style G fill:#5b21b6,stroke:#4c1d95,color:#fff
    style STOP fill:#7f1d1d,stroke:#991b1b,color:#fff
```

## 6. Design rules that outrank convenience

These are the constraints a change must not quietly violate.

**"Not measured" never renders as a number.** A run with no reachable cross-check
cannot detect a fault; a run that never detected has no latency; an unpriced run
is not free; an errored run is not a wrong answer. Each is censored out of its
denominator and reported as `n/a`, never as `0`. A zero in those places reads as
the best possible result.

**The record carries evidence, not conclusions.** A stored run keeps the answer,
the tool calls, the schedule, the resolved model and a fingerprint of the variant
— enough to re-grade it under rules not yet written. Three times this project
stored a verdict instead and lost the ability to re-score its own history.

**The bar is fixed before the numbers.** `GateCriteria` lives inside `TaskSpec`,
so the task fingerprint covers it. Every run record carries that hash; moving a
threshold afterwards changes it and is therefore detectable rather than deniable.

**The instrument is checked before the measurement.** A deliberately degraded
sentinel variant runs in every matrix and must rank last. If it does not, the
board is not read — in either direction. This has already caught a board that
looked fine and meant nothing.

**Cheap checks gate expensive ones.** An offline suite on every commit, a ~$0.03
live probe on every push, and a ~$5 matrix only on demand. Each rung exists
because a confident wrong number costs more than a crash.

## 7. Where it runs

```mermaid
flowchart LR
    subgraph "every commit — $0"
        CI["ci.yml<br/>tests + both fixtures offline"]
    end
    subgraph "every code push — ~$0.03"
        PROBE["live-probe.yml<br/>clean · faulted · instrument check"]
    end
    subgraph "on demand — ~$5"
        MX["live-gauntlet.yml<br/>the matrix"]
    end
    CI --> PROBE --> MX

    style MX fill:#7f1d1d,stroke:#991b1b,color:#fff
```

`live-probe.yml` is the only workflow that touches a credential, and it never
runs on a fork pull request.
