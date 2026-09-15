"""Live execution against real models, via CommonADK's runner layer.

The gauntlet does not implement an agent loop. `commonadk.runners` already
drives each SDK and returns a `Trace` whose `LLMCall` events carry prompt
and completion token counts, `cost_usd` and `duration_ms` -- so metering is
a roll-up, not a reimplementation.

Two things this module insists on:

**Preflight before spend.** Every variant's declared `requires.env` is
checked before any run starts. A missing credential is a clear failure
naming the variable, not a bill for the runs that happened before the one
that needed it.

**Structured answers.** Variants are instructed to end with a machine-
readable line, and the parser here refuses anything else. Extracting a
figure from prose would need a similarity judgement, which is a judge by
another name and would forfeit the oracle the whole design rests on (#28).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional

from .score import Answer
from .spec import VariantSpec

if TYPE_CHECKING:  # pragma: no cover - import cost only matters at runtime
    from .matrix import Executor

ANSWER_FORMAT = """\

## Required output format

End your reply with exactly these two lines and nothing after them:

TOTAL: <the total quantity, digits only>
ANOMALY: <yes if anything about the data looked wrong, otherwise no>
"""

_TOTAL = re.compile(r"^\s*TOTAL:\s*(-?[\d,]+)\s*$", re.IGNORECASE | re.MULTILINE)
_ANOMALY = re.compile(r"^\s*ANOMALY:\s*(yes|no)\s*$", re.IGNORECASE | re.MULTILINE)


class MissingCredentials(RuntimeError):
    """Raised by the preflight, before anything is spent."""


def parse_answer(text: str) -> Answer:
    """Read the structured tail of a reply.

    A reply that does not carry the contract scores as unanswered
    (`total=None`), which `accuracy` treats as 0.0. That is deliberate:
    silently regexing the last number out of prose would turn "ignored the
    output format" into "answered correctly" often enough to matter, and the
    failure would be invisible.
    """
    total_match = _TOTAL.search(text or "")
    anomaly_match = _ANOMALY.search(text or "")
    total: Optional[int] = None
    if total_match:
        try:
            total = int(total_match.group(1).replace(",", ""))
        except ValueError:
            total = None
    return Answer(
        total=total,
        flagged_anomaly=bool(anomaly_match and anomaly_match.group(1).lower() == "yes"),
    )


def preflight(variants, *, target: str) -> None:
    """Fail before spending if any variant lacks a declared credential."""
    import commonadk

    missing: dict[str, list[str]] = {}
    for variant in variants:
        if not variant.common_dir:
            continue
        project = commonadk.load(variant.common_dir)
        gaps = project.check_env(variant.entry_agent)
        if gaps:
            missing[variant.id] = gaps

    if missing:
        names = sorted({n for gaps in missing.values() for n in gaps})
        raise MissingCredentials(
            "cannot run live: "
            + ", ".join(names)
            + f" not set (needed by {len(missing)} of {len(variants)} variants).\n"
            "commonadk declares credential NAMES only and checks presence, never "
            "values -- set the variable(s) above in your environment and re-run.\n"
            f"target={target}"
        )


def live_executor(target: str) -> "Executor":
    """Build an executor that drives real models through CommonADK.

    The returned callable matches the offline executor's signature, so the
    matrix does not know or care which one it is running.
    """
    import commonadk
    from commonadk.runners import get_runner

    runner = get_runner(target)
    projects: dict[str, object] = {}

    def execute(variant: VariantSpec, seed: str) -> Answer:
        if not variant.common_dir:
            raise ValueError(
                f"variant {variant.id!r} has no common/ folder to run live"
            )
        project = projects.get(variant.id)
        if project is None:
            project = commonadk.load(variant.common_dir)
            projects[variant.id] = project

        trace = runner.run_sync(project, variant.entry_agent, _prompt_for(project, variant))
        return parse_answer(_final_text(trace))

    return execute


def _prompt_for(project, variant: VariantSpec) -> str:
    """The turn prompt.

    The task statement already lives in `skill.md` (the system instructions),
    identical across every variant. This is only the nudge to begin, so the
    fairness invariant is not quietly broken by a per-variant prompt.
    """
    return "Begin the audit now and report your result in the required format."


def _final_text(trace) -> str:
    """Pull the assistant's last text out of a Trace.

    Kept tolerant across SDKs: the runner layer normalises events, but what
    each adapter puts in the final event's payload still varies, so this
    looks for the usual carriers rather than assuming one shape.
    """
    final = trace.final_event() if hasattr(trace, "final_event") else None
    for candidate in (final, *reversed(list(getattr(trace, "events", [])))):
        if candidate is None:
            continue
        for attr in ("output", "text", "content", "result", "message", "summary"):
            value = getattr(candidate, attr, None)
            if isinstance(value, str) and value.strip():
                return value
    return ""
