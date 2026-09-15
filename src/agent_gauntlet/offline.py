"""Scripted agent policies -- a test double for the harness, not for agents.

These let the whole pipeline (schedule -> tools -> trace -> score ->
stability) run end-to-end with no API key and no cost, which is what makes
the harness testable in CI. They are emphatically **not** evidence about
how real agents behave: a scripted policy has none of the variance that
makes issue #1 a hard question in the first place. Their only job is to
prove the measurement machinery does what it claims on inputs whose correct
grading is known in advance.

Real execution goes through `commonadk.runners.get_runner(target)`, which
returns a `Trace` carrying token counts, `cost_usd` and durations.
"""

from __future__ import annotations

from typing import Callable

from .interpose import RunContext, ToolTimeout, fetch_quantity, list_record_ids, summary_total
from .score import Answer

Policy = Callable[[], Answer]


def naive() -> Answer:
    """Fetch every record, sum, report. Never cross-checks.

    Under a WRONG_VALUE fault this propagates the falsehood -- the failure
    the gauntlet exists to catch.
    """
    total = 0
    for rid in list_record_ids():
        try:
            total += fetch_quantity(rid)
        except ToolTimeout:
            continue
    return Answer(total=total, flagged_anomaly=False)


def verifying() -> Answer:
    """Sum the records, then reconcile against the independent summary.

    The positive control (#29): it *should* beat `naive` under faults. If a
    difference engineered this deliberately fails to show up, the instrument
    cannot detect subtler ones either.
    """
    total = 0
    for rid in list_record_ids():
        try:
            total += fetch_quantity(rid)
        except ToolTimeout:
            continue
    cross_check = summary_total()
    if cross_check != total:
        return Answer(total=cross_check, flagged_anomaly=True)
    return Answer(total=total, flagged_anomaly=False)


def summary_only() -> Answer:
    """Read the summary and report it, never touching per-record data.

    Immune to fetch-level corruption -- but immune by inattention, not by
    care. It produces UNDETECTED_HARMLESS, the outcome that looks like
    success and is not, and exists here so scoring is tested against it.
    """
    return Answer(total=summary_total(), flagged_anomaly=False)


def sentinel() -> Answer:
    """Deliberately degraded. Must rank last, or the instrument is broken.

    It fails on *quality* -- a plausible-looking undercount -- rather than
    by crashing, so it exercises the same scoring path as a real variant
    (#29, risk 4).
    """
    ids = list_record_ids()
    total = 0
    for rid in ids[: max(1, len(ids) // 2)]:
        try:
            total += fetch_quantity(rid)
        except ToolTimeout:
            continue
    return Answer(total=total, flagged_anomaly=False)


POLICIES: dict[str, Policy] = {
    "naive": naive,
    "verifying": verifying,
    "summary_only": summary_only,
    "sentinel": sentinel,
}


def run_policy(name: str) -> Answer:
    try:
        policy = POLICIES[name]
    except KeyError:
        raise KeyError(
            f"unknown policy {name!r}; known: {sorted(POLICIES)}"
        ) from None
    return policy()


__all__ = ["POLICIES", "Policy", "RunContext", "run_policy", *sorted(POLICIES)]
