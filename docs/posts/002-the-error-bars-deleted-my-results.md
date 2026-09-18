# I added error bars to my eval. They deleted half my own published results.

*[agent-gauntlet](https://github.com/Mehul-Gupta-SMH/agent-gauntlet), part two —
and the arithmetic that changed what I think the tool is for.*

---

Three days ago I published this table. It came from a real run: 324 live agent
executions, nine configurations, a pre-registered bar, a gate that passed.

```
prompt   spread = 15%   naive 50%  -> verifying 65%
toolset  spread = 15%   records 50% -> records+summary 65%
model    spread = 10%   cheap 53%  -> smart 62%
```

Three findings about what makes an agent robust. I wrote them up. I put them
on the front page of the repository.

Then I built the thing that tells you how much a run can actually resolve, and
pointed it at my own record.

## 36 runs, not 324

324 sounds decisive. Across nine configurations it is **36 runs each**. A
per-factor comparison — all the `naive` variants against all the `verifying`
ones — pools four configurations, so 144 runs.

At 95% confidence and 80% power, the smallest difference two groups of 144
binary outcomes can distinguish from noise is **16.5 points**.

| claim | gap | threshold | |
|---|---|---|---|
| sentinel ranks last | 50 pts | 33% | **holds** |
| repair 100% vs 0% | 100 pts | 33% | **holds** |
| repair 22% → 100% across models | 78 pts | 33% | **holds** |
| prompt spread | 15 pts | 16.5% | **inside the noise** |
| toolset spread | 15 pts | 16.5% | **inside the noise** |
| model spread | 10 pts | 16.5% | **inside the noise** |

The headline findings survive. The entire attribution table does not.

Those three numbers were not small effects. They were **unmeasured** — I had
been reporting the width of my own noise and calling it a result.

They are annotated in place, dated, with the arithmetic shown. Not deleted. A
correction nobody can see is not a correction, and the numbers stood without
an error bar because there was no machinery to compute one — hiding that now
would be worse than having lacked it then.

## Why Wilson, and why it matters more than it sounds

The interval I used is the Wilson score interval rather than the normal
approximation everyone reaches for first. The reason is the entire problem in
miniature.

At an observed rate of **0%**, the normal approximation produces an interval
of **zero width**.

So a board reports `propagation 0% ± 0` after eight runs. Perfect confidence,
from almost no evidence, formatted as rigour. It would sit directly in the
column that gates the product.

Wilson, on the same eight runs, says `[0%, 32%]`.

That is the honest answer, and it is unflattering in exactly the place a
measurement tool most needs to be.

## The finding that reframed the project

With intervals on the board, I looked at the fixture that *passes* its
pre-registered gate:

```
smart · verifying · +summary    93%  [82%, 97%]
cheap · verifying · +summary    74%  [61%, 84%]
```

Those overlap. The gate passed — median Kendall's tau 0.845, top-1 stability
100% — and the top two configurations are not separated.

Both things are true, and they are different measurements. The gate asks
whether the *ranking* survives a change of random seed. It does not ask whether
any particular pair is distinguishable. I had never made that distinction out
loud, and a reader would take the order as the result.

## Leaderboards are expensive. Regression tests are not.

Then I ran the arithmetic the other way: how many runs would I need?

```
to detect a drop of   runs per configuration
        30%                    44
        20%                    99
        10%                   393
         5%                  1570
```

To separate nine configurations on a leaderboard, you have to resolve the gaps
*between* them — and you do not get to choose how close together they are.
Real configurations differ by ten points all the time. At 393 live runs each,
that is not a product.

**Regression testing is the same arithmetic with the constraint inverted.**

You compare one configuration against its own past, and you choose the
threshold: *tell me if robustness drops by 20 points.* That is 99 runs. It is
affordable, it is repeatable, and it answers the question people actually have.

Because here is the failure mode nobody has a test for. Someone edits a prompt
six weeks after launch. The agent still produces plausible output. Every
existing test passes, because every existing test checks whether the output
*looks* right. Nothing checks whether the agent still refuses to believe a
corrupted tool result.

An agent's robustness is a property that can regress silently. Ranking agents
against each other is the interesting demo. Noticing that one of them quietly
got worse is the useful product.

I only know that because the error bars contradicted me.

## What I am not claiming

The resolution limits above range over **seed and repeat variance, on one
scenario**. Not over tasks, not over model drift, not over provider
nondeterminism. A width quoted without that scope is the same overclaim as an
uncensored zero.

The regression half is arithmetic, not a shipped feature. Comparing two runs,
storing a certificate and gating on a drop are issues
[#33](https://github.com/Mehul-Gupta-SMH/agent-gauntlet/issues/33)–[#35](https://github.com/Mehul-Gupta-SMH/agent-gauntlet/issues/35);
the intervals and the resolution limit are built, the comparison is not.

And I have never actually caught a regression with this tool. Until I have,
"regression testing for agent robustness" is a design, not a result. That
experiment is next, and it will be published whichever way it goes.

## Try it

```bash
pip install -e .
gauntlet run fixtures/inventory/audited.yaml --repeats 3 --seeds 3
```

Every run that produced a number in this post is committed with its raw
output, including the ones that turned out to be noise:

**[github.com/Mehul-Gupta-SMH/agent-gauntlet](https://github.com/Mehul-Gupta-SMH/agent-gauntlet)**
