# Prior art, and what is actually left (#25)

Written because the README claimed a differentiator that prior work covers, and
a project whose whole argument is *do not trust a number because it looks
reassuring* has no business making an unchecked novelty claim about itself.

Everything below was verified against the primary listing in October 2026, not
recalled. Where only secondary write-ups were reachable, that is said.

## What the README used to claim

> **Chaos is scored, not smoke-tested.** Fault injection is the measurement, not
> a robustness check bolted on afterwards. It is what creates the oracle.

Three separable assertions. **All three are prior art.**

### "Fault injection is the measurement"

| Work | What it establishes |
|---|---|
| [MAS-FIRE](https://arxiv.org/abs/2602.19843) (Feb 2026) | 15-fault taxonomy for LLM multi-agent systems, split intra-/inter-agent, injected by prompt modification, response rewriting and message-routing manipulation. Explicitly targets semantic failures that propagate without raising exceptions. |
| [ReliabilityBench](https://arxiv.org/abs/2601.06112) (Jan 2026) | Chaos-engineering fault injection at intensity λ per tool call — timeouts, rate limits, partial responses, schema drift — alongside `pass^k` and robustness to rewording at intensity ε. Claims to be first to apply chaos engineering to LLM agent evaluation. |
| [AgentChaos](https://arxiv.org/abs/2608.06790) (Aug 2026) | Six fault types in three categories (crash / omission / value) injected at the HTTP layer, 65 fault configurations across five agent systems and seven benchmarks. `pass@1` drops up to 50 points. Concludes robustness depends more on architecture than on model. |
| [ToolMisuseBench](https://arxiv.org/abs/2604.01508) (Apr 2026) | Offline benchmark with deterministic replay; faults separated into schema drift, rate limits, timeouts, authorization failures and adversarial error rewriting. |

### "It is what creates the oracle"

This was the part that looked distinctive. It is not.

| Work | What it establishes |
|---|---|
| [CatchBench](https://arxiv.org/abs/2608.22808) (Aug 2026) | Labels each case **by its injection site, correct by construction and independent of any detector**. That is "own the oracle", published. It also notes injected runs look like clean runs at the run level, so a global signal cannot stand in for the specific fault. |
| [AgentCheck](https://arxiv.org/abs/2607.11098) (Jul 2026) | Records a clean run, replays it with **one tool response altered**, and reports the first step where the two runs diverge. The counterfactual pair. (Its trajectories are still checked with fault-specific checks, so not purely judge-free.) |
| [SSCBench](https://arxiv.org/abs/2610.11514) (Oct 2026) | Clean trajectories as **negative controls in the oracle reference set**, and a seed is run under fault injection only if that configuration first solves the clean task. |
| [arXiv 2608.13867](https://arxiv.org/abs/2608.13867) (Aug 2026) | Treats a same-workload no-fault run as the control and reads the difference as the injection's effect; suggests keeping a content digest from the clean run for exact comparison. |

### The denominator correction, also published

AgentChaos excludes runs where the fault never fired, on the grounds that
including them *"understates fault impact by mixing unaffected tasks into the
denominator"* (per secondary write-ups; the arXiv page was not reachable
directly). That is this project's reachability guard and decidable-denominator
rule, independently arrived at and already in print.

## What #25 proposed instead, and why that fails too

> The honest claim narrows from *"we inject faults"* to *"we search config space
> under fault injection and hand back the config."*

Two problems.

1. **This project does not search.** The README's own second differentiator says
   so: *"materializable — not yet searchable."* It enumerates a declared grid.
   Claiming search would be a worse error than the one being corrected.
2. **Joint config × fault search exists**, in distributed systems:
   [CAFault](https://www.usenix.org/system/files/atc25-chen-yuanliang.pdf)
   (USENIX ATC 2025) treats it as a product of two spaces and prunes with a
   fault-dependent model before fuzzing fault inputs per configuration.
   [RobustSGPO](https://arxiv.org/abs/2609.09646) (Sep 2026) searches agent
   harness configurations with explicit search-space control — without fault
   injection. DSPy optimizes prompts as parameters.

## What is actually left

Narrow, and stated as what has not been found rather than as what does not
exist. A literature sweep is evidence of absence only in proportion to its
thoroughness, and this one was four searches.

1. **It materializes and runs the configurations it measures.** Every work above
   evaluates a system you bring it. This one generates variants from a factor
   grid, writes a runnable project per cell onto a framework-neutral
   materializer, and hands back the winning one as a directory. That is an
   engineering difference, not a research contribution, and it is checkable by
   running it.
2. **The reporting discipline, which is the part worth copying.** Unmeasured
   values render `n/a` and never `0`. The rank column stops where the intervals
   stop. A winner the run cannot separate from its rivals is refused rather than
   named. Denominators are decidable-only. Bars are fingerprinted before the
   numbers exist. None of that is novel in statistics; it is uncommon in agent
   benchmarks, and it is the thing this repository actually demonstrates.

## Findings from the sweep worth acting on

- **MAS-FIRE**: iterative closed-loop topologies neutralize 40%+ of faults that
  cause catastrophic collapse in linear workflows. **AgentChaos** independently
  concludes robustness depends more on architecture than on model. Two
  independent results pointing the same way: topology may be the dominant
  robustness factor, which is evidence for #23 and a warning that a fixed
  topology with variable nodes measures the less important half.
- **#5 should adapt MAS-FIRE's taxonomy** rather than inventing one, and
  AgentChaos's crash / omission / value split is a cleaner three-way cut than
  the one in use here.
- **CatchBench's framing** — "when can a failure be caught?" — is #16's
  detectability question with a benchmark already built for it.

## The steal-list

Kept from #25, unverified beyond general knowledge; spot-check before relying on
specifics.

| Need | Project | What to take |
|---|---|---|
| Harness architecture | Inspect AI (UK AISI) | Task/Solver/Scorer split, Docker sandboxing, log format; `epochs` + reducers (mean, at_least_k) is #1's repeat problem already solved |
| Reliability metric | τ-bench (Sierra) | `pass^k`; database-state reward — an objective oracle of exactly this shape |
| Hypothesis schema | Chaos Toolkit | A literal `steady-state-hypothesis` block — #17's object, already specified |
| State faults | Jepsen | Checks a recorded *history* against a model, not just final state |
| Scoring analogy | Mutation testing (PIT, mutmut) | Its equivalent-mutant problem is #16's detectability requirement with decades of literature |
| Factor attribution | Optuna | `optuna.importance` (fANOVA) — a shipped alternative to Shapley (#22) |
| Multi-objective board | Ax / BoTorch | Pareto-front multi-objective optimization — #11's problem |
| Judge de-biasing | AlpacaEval, Chatbot Arena | Length-controlled win rates for #3; Bradley-Terry with CIs and explicit ties for #11 |
| Interposer | Toxiproxy | A well-executed fault-injecting proxy |
| Workflow replay | Temporal `WorkflowReplayer` | Re-run recorded histories against new code — #19's mechanism |
| Positioning risk | DSPy, RobustSGPO | Config/prompt optimization without fault injection — position against both explicitly |
| Prompt injection | AgentDojo (ETH) | Purpose-built for #5's adversarial class |

**If you read three:** CatchBench (the oracle argument, made first), AgentChaos
(the denominator correction and the architecture finding), τ-bench (how to
measure). Inspect AI fourth, for how to build it.
