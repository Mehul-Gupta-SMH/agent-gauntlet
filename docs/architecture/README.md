# Architecture

Two documents, different altitudes.

| | For | Answers |
|---|---|---|
| [**HLD**](hld.md) | Reading the project for the first time | What are the pieces, what flows between them, and why is it shaped like this? |
| [**LLD**](lld.md) | Changing the code | What does each module own, what are the data structures, and which invariants must a change not break? |

Design *discussion* — open questions, trade-offs, things not yet settled —
lives in [`plan.md`](../../plan.md) and the issue tracker. These two describe
what exists.

## The shortest possible summary

agent-gauntlet generates many agent configurations for one task, runs them all
against the same scenarios while **lying to them through their own tools**, and
ranks what survives.

The lie is the point. Because the harness injected the falsehood, it knows the
truth, so "did this agent's answer swallow the lie?" is decided by comparison
rather than by a judge. That single property is what the rest of the
architecture is arranged around.

```mermaid
flowchart LR
    SPEC["task spec<br/>+ pre-registered bar"] --> ARCH["architect<br/><i>generate variants</i>"]
    ARCH --> MATRIX["matrix<br/><i>run every cell</i>"]
    CHAOS["chaos<br/><i>seeded faults</i>"] --> MATRIX
    MATRIX --> LEDGER[("ledger<br/><i>append-only</i>")]
    LEDGER --> SCORE["score<br/><i>oracle, no judge</i>"]
    SCORE --> BOARD["board<br/><i>rank · attribute · gate</i>"]
    BOARD --> OUT["verdict<br/>+ exported winner"]

    style CHAOS fill:#7f1d1d,stroke:#991b1b,color:#fff
    style LEDGER fill:#1e3a5f,stroke:#1e40af,color:#fff
    style OUT fill:#14532d,stroke:#166534,color:#fff
```
