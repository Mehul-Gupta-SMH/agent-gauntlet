"""Pin the rates a dollar figure was computed against (#14).

Cost is the most falsifiable number this board reports and the fastest to
go stale. The rates come from `commonadk.runners.pricing`, whose own
docstring is unambiguous about what it is:

    **This is a snapshot, not a live price feed.** [...] It WILL drift out
    of date -- provider pricing changes more often than this file will be
    updated.

Until now nothing on this side recorded *which* snapshot a run was priced
against. Two matrices run a month apart averaged into one cost column with
no way to tell that the rates had moved underneath them, and `$0.0041` was
printed with the same confidence either way.

#14 states the smallest defensible position: pin at run time, show the
table's identity beside any dollar figure, and treat cross-run dollar
comparisons as invalid unless the pinned tables match. This is that, and
the third part is the one with teeth -- a variant whose runs were priced
two different ways reports no cost per run at all, exactly as an unpriced
one does. Not measured and measured-differently are both "not a number you
can compare".

**The rates are read through the public function, not the private dict.**
`estimate_cost_usd(model, 1_000_000, 0)` is the input rate per million by
construction, and `(0, 1_000_000)` the output rate. Probing the behaviour
rather than the table means this keeps working if upstream renames or
restructures its storage, and records what the pricing actually DID rather
than what a dict was hoped to contain.

There is no date to show, because upstream publishes none. A fingerprint
of the rates in effect is what exists, and it answers the question that
matters -- did these two runs use the same numbers -- without inventing a
date nobody recorded.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

from pydantic import BaseModel, Field

PER_MILLION = 1_000_000

TABLE = "commonadk.runners.pricing"
"""The static rate table. Probed, hashed, and pinned."""

SDK_PRICED = "sdk:ResultMessage.total_cost_usd"
"""The Claude Agent SDK computes its own cost, and commonadk defers to it.

From `commonadk/runners/claude_agent.py`, verbatim:

    `cost_usd` comes straight from `ResultMessage.total_cost_usd` -- this
    SDK computes cost itself [...]; `runners/pricing.py` is never consulted
    for this target, even when tokens are known, since a per-model
    `costUSD` the SDK itself computed is strictly more authoritative than
    this project's own static table.

Which makes the pin this module was built to take *wrong on the only
target this project has ever run live*. `--target claude` is the default;
every live dollar in every ledger came from the SDK's figure, and every
record claimed it came from a table whose rates had no influence on it.

The defect is the module docstring's own promise inverted. It says the
rates are probed so as to record "what the pricing actually DID rather
than what a dict was hoped to contain" -- and then recorded a dict that
was never consulted. Plausible rates, a real fingerprint, a healthy-looking
record: the fail-green shape again.
"""

AUTHORITY: dict[str, str] = {
    "claude": SDK_PRICED,
    "autogen": TABLE,
    "crewai": TABLE,
    "google_adk": TABLE,
    "langgraph": TABLE,
    "openai_agents": TABLE,
}
"""Which component actually produced `rollup["cost_usd"]`, per target.

Declared rather than probed, because the only honest probe would be to
make a paid call and compare -- and a target missing from this map records
`UNKNOWN` rather than being assumed to use the table. Assuming is how the
wrong authority got pinned in the first place.
"""

UNKNOWN = "unrecorded"
"""No authority is recorded for this target: it is absent from
`AUTHORITY`. A gap in the map, and deliberately not read as the table."""

MIXED = "mixed"
"""These records were priced by MORE THAN ONE authority.

A different thing from `UNKNOWN`, and it used to collapse into it -- so a
ledger whose rows came from two authorities printed "this target is not in
`pricing.AUTHORITY`", naming a cause that was not the cause.

It matters because the two authorities do not agree. Measured over five
live `claude` probes (experiment 015), `ResultMessage.total_cost_usd` comes
in at **1.83x to 2.29x** the figure this table computes from the same runs'
recorded token counts. So a cost column mixing them is not approximate
across rows, it is off by about a factor of two on some of them -- and
`--max-cost-per-run` and `board.best_under` pick winners on it.
"""


class PriceSnapshot(BaseModel):
    """The rates in effect when a run was made, and a hash of them."""

    rates: dict[str, Optional[list[float]]] = Field(default_factory=dict)
    """Resolved model id -> [usd per 1M input, usd per 1M output].

    None for a model the table does not price. Recorded rather than
    dropped: "this model was unpriced at the time" is a fact about the run,
    and a later table that prices it must not make the old run look like it
    had been priced all along.
    """
    fingerprint: str = ""
    taken_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    source: str = TABLE
    """What actually produced the dollars -- see `AUTHORITY`.

    Defaults to the static table because that is what every non-Claude
    target uses, and because a record written before this field existed was
    written under that assumption. It stays out of `fingerprint()` at that
    default for the usual reason: no existing pin may move.
    """

    @property
    def detects_drift(self) -> bool:
        """Can two runs under this snapshot be told apart if rates moved?

        Only when there is a table to hash. Under `SDK_PRICED` the figure
        comes from a component that publishes no version and no rates, so a
        price change upstream is invisible from here -- a blind spot to
        state rather than a pin to imply (#14, #47).
        """
        return self.source == TABLE and bool(self.rates)

    @property
    def models_cache_and_batch_tiers(self) -> bool:
        """Does the authority account for cache reads and batch discounts?

        The static table prices input and output flat: no cache tier, no
        batch tier (#47). Every variant here shares an identical task
        statement by the fairness invariant, so the grid is built out of
        stable prefixes -- exactly the shape caching discounts -- and the
        table prices them all the same.

        `SDK_PRICED` reports what the SDK billed, so whatever discounts the
        run actually obtained are in the figure. That is not a claim that
        the SDK models any particular tier; it is the weaker and checkable
        claim that this project is not the one doing the arithmetic.
        """
        return self.source == SDK_PRICED


def rates_for(models: Iterable[str]) -> dict[str, Optional[list[float]]]:
    """Probe the live pricing function for each model's two rates."""
    try:
        from commonadk.runners.pricing import estimate_cost_usd
    except Exception:       # pragma: no cover - upstream shape
        return {}

    out: dict[str, Optional[list[float]]] = {}
    for model in sorted({m for m in models if m}):
        try:
            low = estimate_cost_usd(model, PER_MILLION, 0)
            high = estimate_cost_usd(model, 0, PER_MILLION)
        except Exception:   # pragma: no cover - defensive
            low = high = None
        out[model] = None if low is None or high is None else [low, high]
    return out


def authority_for(target: Optional[str]) -> str:
    """Which component priced a run on `target`. `UNKNOWN` if unlisted."""
    if not target:
        return UNKNOWN
    return AUTHORITY.get(target, UNKNOWN)


def fingerprint(
    rates: Mapping[str, Optional[Sequence[float]]],
    *,
    source: str = TABLE,
) -> str:
    """A stable hash of the rates, so two runs can be compared as priced.

    Built from named fields in sorted order for the same reason
    `TaskSpec.fingerprint` is: a hash of a whole object moves when
    something unrelated is added, and then no historical record resolves to
    anything.
    """
    body: dict[str, Any] = {
        k: (list(v) if v is not None else None) for k, v in sorted(rates.items())
    }
    # The authority joins the payload only when it is not the table, so
    # every snapshot taken against the table keeps the fingerprint its
    # records were written under. Same rule as `TaskSpec.fingerprint`.
    payload = json.dumps(
        body if source == TABLE else {"rates": body, "source": source},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def snapshot(models: Iterable[str], *, target: Optional[str] = None) -> PriceSnapshot:
    """Pin what priced this run, which is not always a table (#47).

    Under `SDK_PRICED` there are no rates to record, and recording the
    table's anyway is what this module did wrong: it pinned numbers that
    had no influence on a single dollar in the ledger. An empty `rates`
    under a named non-table source says "priced, by something that
    publishes no rates", which is the truth and is distinguishable from
    both "unpriced" and "priced against these numbers".
    """
    source = authority_for(target)
    rates = {} if source == SDK_PRICED else rates_for(models)
    return PriceSnapshot(
        rates=rates, source=source, fingerprint=fingerprint(rates, source=source),
    )


def pinned(record: Any) -> Optional[str]:
    """The price fingerprint a run was recorded under, if any.

    Offline runs have none and spend nothing, which is why "no fingerprint"
    is not itself drift.
    """
    prices = getattr(record, "prices", None) or {}
    got = prices.get("fingerprint")
    return got or None


def drift(records: Sequence[Any]) -> dict[str, list[str]]:
    """Price fingerprints seen, mapped to the variants that used them.

    More than one key means the ledger holds runs priced against different
    rate tables. Their dollar figures are individually truthful and
    jointly meaningless, which is the distinction #14 is about.
    """
    seen: dict[str, set[str]] = {}
    for r in records:
        got = pinned(r)
        if got is None:
            continue
        seen.setdefault(got, set()).add(r.variant_id)
    return {k: sorted(v) for k, v in sorted(seen.items())}


__all__ = ["AUTHORITY", "PER_MILLION", "PriceSnapshot", "SDK_PRICED", "TABLE",
           "UNKNOWN", "authority_for", "drift", "fingerprint", "pinned",
           "rates_for", "snapshot"]
