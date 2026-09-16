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

import ast
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


_NEVER_THE_PROVIDER = (
    ImportError, AttributeError, TypeError, ValueError,
    KeyError, IndexError, OSError, NotImplementedError,
)
"""Failures a remote service cannot cause.

A missing package, a bad attribute, a wrong signature -- these are always
this repo or its environment. `ModuleNotFoundError` is an `ImportError`,
which is the specific case that shipped a green probe having called nothing.
`OSError` covers `FileNotFoundError`; the connection errors that subclass it
are caught by the markers below, which are checked first.
"""

_PROVIDER_SHAPED = (
    "timeout", "timed out", "connection", "unreachable", "overloaded",
    "rate limit", "rate_limit", "too many requests", "quota",
    "429", "500", "502", "503", "529",
    "service unavailable", "temporarily unavailable", "try again",
    "apiconnection", "apistatus", "apitimeout", "apierror",
    "remotedisconnected", "ssl", "econnreset", "authentication",
    "unauthorized", "invalid api key", "credit balance",
)
"""Substrings that positively mark a failure as the provider's or the
network's, matched against the exception type name and its message.

Deliberately a positive test. The alternative -- treat anything we do not
recognise as an outage -- is how a probe reports "provider outage" for a
missing import.
"""


def provider_unreachable(exc: BaseException) -> bool:
    """Did the model genuinely fail to answer, for reasons not ours?

    Unknown failures are OURS. A false red is annoying and actionable; a
    false green is invisible, and the point of the probe is to be believed.

    The whole chain is inspected, not just the outermost exception: SDKs
    wrap, and a lazily-imported dependency surfaces as a `RuntimeError`
    whose `__cause__` is the `ModuleNotFoundError` that actually explains it.
    """
    chain: list[BaseException] = []
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        chain.append(cur)
        cur = cur.__cause__ or cur.__context__

    # Checked before the never-the-provider types because these subclass
    # OSError, which is in that tuple for FileNotFoundError's sake.
    if any(isinstance(e, (ConnectionError, TimeoutError)) for e in chain):
        return True

    # Any import/type/attribute failure anywhere in the chain settles it.
    # This also stops a message from matching by accident -- an
    # `ImportError: cannot import name 'Timeout'` contains "timeout".
    if any(isinstance(e, _NEVER_THE_PROVIDER) for e in chain):
        return False

    haystack = " ".join(f"{type(e).__name__} {e}" for e in chain).lower()
    return any(marker in haystack for marker in _PROVIDER_SHAPED)


class MissingCredentials(RuntimeError):
    """Raised by the preflight, before anything is spent."""


def normalize_reply(text: str) -> str:
    """Flatten a reply that arrived as stringified content blocks.

    Observed live on the langgraph target: `RunFinished.final_text` carried
    `repr()` of the model's content-block list --

        [{'signature': ..., 'thinking': '', 'type': 'thinking'},
         {'text': '...\\nTOTAL: 107\\nANOMALY: no', 'type': 'text'}]

    -- so the newlines were backslash escapes inside a Python literal and no
    line-anchored pattern could match, even though the model had followed
    the output contract exactly. Parsing that as "the model ignored the
    format" would have blamed the model for a harness defect and scored a
    perfectly good run as unanswered.

    `ast.literal_eval` is used rather than `eval`: it evaluates literals
    only and cannot execute code from a model's output.

    Only `type == "text"` blocks are kept. Thinking blocks are deliberately
    excluded -- scoring an agent on its reasoning rather than its answer
    would measure something else entirely.
    """
    stripped = (text or "").strip()
    if not stripped.startswith(("[", "{")):
        return text or ""
    try:
        parsed = ast.literal_eval(stripped)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return text or ""

    blocks = parsed if isinstance(parsed, list) else [parsed]
    texts = [
        b["text"] for b in blocks
        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
    ]
    return "\n".join(texts) if texts else (text or "")


def parse_answer(text: str) -> Answer:
    """Read the structured tail of a reply.

    A reply that does not carry the contract scores as unanswered
    (`total=None`), which `accuracy` treats as 0.0. That is deliberate:
    silently regexing the last number out of prose would turn "ignored the
    output format" into "answered correctly" often enough to matter, and the
    failure would be invisible.
    """
    flat = normalize_reply(text)
    total_match = _TOTAL.search(flat)
    anomaly_match = _ANOMALY.search(flat)
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
        affected = sorted(missing)[:4]
        more = len(missing) - len(affected)
        raise MissingCredentials(
            "cannot run live: "
            + ", ".join(names)
            + f" not set (needed by {len(missing)} of {len(variants)} variants).\n"
            f"  affected: {', '.join(affected)}" + (f" (+{more} more)" if more else "")
            + "\n\ncommonadk declares credential NAMES only and checks presence, "
            "never values.\nEither set the variable(s) above, or choose a model "
            "grid that needs only\nthe credentials you have -- e.g.\n"
            "  --models cheap=anthropic/claude-haiku-4-5 "
            "smart=anthropic/claude-sonnet-5\n\n"
            "The whole matrix is refused rather than run partially: an uneven "
            "grid would\nchange what is being compared.\n"
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
        # The rollup travels WITH the answer. It used to be dropped here --
        # `run_sync` returns token counts, cost_usd and durations, and this
        # function threw all of it away, so a $5 live matrix recorded no
        # spend at all and the board's cost column read n/a (#14).
        return parse_answer(_final_text(trace)), _rollup(trace)

    return execute


def _rollup(trace) -> dict:
    """`Trace.rollup()`, or an empty dict if it cannot be read.

    Metering must never be able to fail a run that otherwise succeeded: a
    missing cost number is a gap in the record, and `cost_complete` already
    renders that honestly. Losing the answer over it would be worse.
    """
    try:
        return dict(trace.rollup())
    except Exception:  # pragma: no cover - defensive; shape is upstream's
        return {}


def _prompt_for(project, variant: VariantSpec) -> str:
    """The turn prompt.

    The task statement already lives in `skill.md` (the system instructions),
    identical across every variant. This is only the nudge to begin, so the
    fairness invariant is not quietly broken by a per-variant prompt.
    """
    return "Begin the audit now and report your result in the required format."


def _final_text(trace) -> str:
    """Pull the assistant's last text out of a Trace.

    Reads the documented carriers rather than guessing: `RunFinished` is the
    terminal successful event and carries `final_text`; `AgentFinished`
    carries `output_summary` per agent. An earlier version of this function
    probed for plausible-sounding attributes (`output`, `text`, `content`,
    ...) -- none of which exist on these events, so every run would have
    parsed as unanswered and an entire paid matrix would have scored zero.

    Returns "" when the run produced no text, which `parse_answer` turns
    into an unanswered result rather than a guess.
    """
    from commonadk.runners import AgentFinished, RunFinished

    events = list(getattr(trace, "events", []) or [])

    for event in reversed(events):
        if isinstance(event, RunFinished) and event.final_text:
            return event.final_text

    for event in reversed(events):
        if isinstance(event, AgentFinished) and event.output_summary:
            return event.output_summary

    return ""
