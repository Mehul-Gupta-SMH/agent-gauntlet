# Low-level design

For changing the code. [HLD](hld.md) for why it is shaped this way.

## 1. Module map

Dependencies point downward; nothing below imports anything above it.

```mermaid
flowchart TD
    CLI["<b>cli</b> · 751<br/>argparse · board rendering · probe"]
    BOARD["<b>board</b> · 504<br/>summarize · rank · pareto · held-out"]
    ANALYZE["<b>analyze</b> · 172<br/>kendall_tau · stability"]
    MATRIX["<b>matrix</b> · 235<br/>run_matrix · _attempt"]
    LEDGER["<b>ledger</b> · 259<br/>RunRecord · rescore"]
    LIVE["<b>live</b> · 290<br/>executor · parsing · classification"]
    OFFLINE["<b>offline</b> · 171<br/>scripted policies"]
    ARCH["<b>architect</b> · 369<br/>PROMPTS · TOOLSETS · generate"]
    SCORE["<b>score</b> · 296<br/>Outcome · score_run"]
    INTER["<b>interpose</b> · 227<br/>RunContext · tool surface"]
    FAULTS["<b>faults</b> · 178<br/>FaultSchedule"]
    SPEC["<b>spec</b> · 240<br/>TaskSpec · VariantSpec"]

    CLI --> BOARD & MATRIX & ARCH & LIVE
    BOARD --> LEDGER & ANALYZE
    MATRIX --> LEDGER & SCORE & LIVE & OFFLINE & INTER & FAULTS
    LEDGER --> SCORE & FAULTS & SPEC
    LIVE --> SCORE & SPEC
    OFFLINE --> INTER & SCORE
    ARCH --> SPEC
    SCORE --> INTER & FAULTS & SPEC
    INTER --> FAULTS

    style SPEC fill:#1e3a5f,stroke:#1e40af,color:#fff
    style FAULTS fill:#7f1d1d,stroke:#991b1b,color:#fff
```

## 2. Data model

```mermaid
classDiagram
    class TaskSpec {
        +str id
        +str statement
        +Oracle oracle
        +list~Scenario~ scenarios
        +int tolerance
        +GateCriteria gate
        +fingerprint() str
    }
    class Scenario {
        +str id
        +dict records
        +list~str~ audited
        +expected_total int
        +audited_total int
        +audited_ids list
    }
    class GateCriteria {
        +float min_median_tau
        +float min_top1_stability
        +int min_seeds
        +str set_by
        +str set_at
    }
    class VariantSpec {
        +str id
        +str common_dir
        +dict factors
        +str model
        +str fingerprint
        +bool is_sentinel
    }
    class RunRecord {
        +str task_fingerprint
        +str variant_fingerprint
        +str model
        +str seed
        +str condition
        +FaultSchedule schedule
        +Score score
        +Answer answer
        +str error
        +int attempts
        +list tool_calls
        +dict rollup
        +base_seed str
    }
    class Score {
        +bool correct
        +float accuracy
        +Outcome outcome
        +bool propagated
        +bool repaired
        +bool detected
        +bool surfaced
        +int detect_latency
    }

    TaskSpec "1" *-- "n" Scenario
    TaskSpec "1" *-- "1" GateCriteria
    RunRecord "1" *-- "1" Score
    VariantSpec ..> RunRecord : identifies
```

### Two fingerprints, two jobs

| | Covers | Answers |
|---|---|---|
| `TaskSpec.fingerprint()` | statement, scenarios, oracle, tolerance, **gate** | "Was this run judged against the bar that was set in advance?" |
| `VariantSpec.fingerprint` | realized `skill.md`, tool grant, resolved model, entry agent | "Is this the same agent as that other run called by the same name?" |

Both are built **field by field**, not by dumping the model. Hashing a whole
model means adding a field rehashes specs that do not use it, which detaches every
historical record from any task that still exists. A new field joins the payload
only when set.

## 3. Scoring

`score_run(task, scenario, schedule, ctx, answer) -> Score` is a pure function of
things the ledger stores, which is what makes `ledger.rescore()` possible.

Two observables; everything else derives from them:

```python
repaired = (not clean) and exposed and correct        # the oracle's verdict
detected = exposed and (surfaced or repaired)         # derived, not primitive
```

`surfaced` is the agent's claim (`flagged_anomaly`). `repaired` is the oracle's.
Detection is not directly observable — it is inferred from what the agent did
about the fault — so it is a label over the two, not a third measurement.

```mermaid
stateDiagram-v2
    [*] --> CLEAN: no fault injected
    [*] --> exposed: a lie reached the agent
    [*] --> UNDETECTED_HARMLESS: never touched it

    exposed --> repaired: answer is correct
    exposed --> not_repaired: answer is wrong

    repaired --> SURFACED_AND_REPAIRED: flagged
    repaired --> SILENTLY_REPAIRED: silent
    not_repaired --> SURFACED_BUT_PROPAGATED: flagged
    not_repaired --> UNDETECTED_PROPAGATED: silent, tracks the lie
    not_repaired --> UNDETECTED_HARMLESS: silent, wrong otherwise
```

The bottom-right pair is an honest limit: an agent that neither says nor fixes
anything is indistinguishable from one that saw nothing, so both fall to the
undetected branch rather than getting an invented bucket.

### Band rules

`propagated` and `propagation_determinable` both use a band of
`max(tolerance, 10% of expected)`. An agent can swallow the lie *and* miscount, so
exact equality would let the worst case through on a rounding error. When the
corruption is smaller than twice the band, "trusted the lie" and "counted slightly
wrong" are the same number — those runs are **undecidable** and leave the
denominator rather than scoring as a clean 0%.

## 4. Censoring: the rule with the most edge cases

Five things are excluded from denominators rather than counted as failures.

| Excluded | When | Why not zero |
|---|---|---|
| detection | no reachable cross-check | detection was impossible, not missed |
| repair | not exposed, or no cross-check | nothing to repair |
| propagation | corruption below the decidability band | the two hypotheses are the same number |
| latency | never detected | `0` reads as "noticed instantly" |
| everything | the run errored | the agent never got to answer |
| cost | any run in the variant was unpriced | the total is a floor, not a cost |

Anything censored renders `n/a` and reports its `n`. **A zero in these positions
reads as the best possible result**, which is exactly backwards.

## 5. Fairness invariants (`matrix.run_matrix`)

Enforced centrally rather than left to callers:

- Every variant faces **the same scenarios** and the same fault seeds.
- `seed_for` includes the variant id, so two variants never draw the same
  corrupted value — a ranking cannot be an artifact of one drawing an easier lie.
- It excludes the condition, so a clean/faulted pair stays twinned.
- Faults land only on **audited** records, so a fault always contradicts something
  reachable and censoring stays a property of the tool set alone.
- The task statement is byte-identical across variants; only prompt strategy,
  tool grant and model differ.

### Retry policy

```mermaid
flowchart TD
    E["execute(variant, seed)"] --> OK{"raised?"}
    OK -->|no| DONE["record answer + rollup"]
    OK -->|yes| P{"provider-shaped?"}
    P -->|"no — ImportError,<br/>TypeError, …"| ERR["record error, attempts=1"]
    P -->|yes| N{"attempts left?"}
    N -->|yes| B["sleep backoff · 2ⁿ"] --> E
    N -->|no| ERR

    style ERR fill:#78350f,stroke:#92400e,color:#fff
```

Unknown failures are treated as **ours** and not retried. A false red is
actionable; a false green is invisible.

## 6. Tool grants

`TOOLSETS` is enforced, not documentation. `RunContext.require()` raises
`ToolUnavailable` for anything outside the grant, so a declared factor is a real
constraint rather than a label — and the censoring logic built on it is real too.

| Tool set | Grants | Purpose |
|---|---|---|
| `records` | `list_records`, `fetch_record` | no cross-check — detection impossible in principle |
| `records+summary` | + `get_summary`, `list_audited_records` | detection and repair possible |
| `records-partial` | `list_records_sample`, `fetch_record` | **the sentinel** — sees half the records |

The sentinel is degraded by a **missing capability**, never by a prompt. A prompt
asking a capable model to be careless is ignored; this has been measured.

## 7. Invariants a change must not break

1. `score_run` stays a pure function of stored fields, or `rescore` dies.
2. No judge in `score`. Any similarity threshold forfeits the oracle.
3. Censored values render `n/a`, never `0`.
4. Fingerprints are built field by field; new fields join only when set.
5. The sentinel must be able to rank last — check it live, not only offline.
6. Both executors keep one signature, returning `Answer` or `(Answer, rollup)`.
7. `Ledger` is append-only. Records are never rewritten.

## 8. Test layout

```
test_faults      seeded determinism, plausible corruption, target restriction
test_score       the outcome cross-product, band rules, censoring
test_repair      repair vs detection, the broken proxy, metering
test_matrix      fairness invariants, fingerprint stability
test_resilience  retries, errored records, censoring of failures
test_board       aggregation, gating, ties, attribution, export
test_board_costs cost honesty, latency censoring
test_heldout     the winner's curse
test_live        parsing, preflight, probe exit codes
test_probe_*     faulted half, instrument check
test_audited     the hardened fixture
test_variant_fingerprint  prompt drift detection
```

Many are regression tests built from real defects. The comments naming those
defects are the point — they explain why an assertion that looks pedantic exists.
