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
    source: str = "commonadk.runners.pricing"


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


def fingerprint(rates: Mapping[str, Optional[Sequence[float]]]) -> str:
    """A stable hash of the rates, so two runs can be compared as priced.

    Built from named fields in sorted order for the same reason
    `TaskSpec.fingerprint` is: a hash of a whole object moves when
    something unrelated is added, and then no historical record resolves to
    anything.
    """
    payload = json.dumps(
        {k: (list(v) if v is not None else None) for k, v in sorted(rates.items())},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def snapshot(models: Iterable[str]) -> PriceSnapshot:
    rates = rates_for(models)
    return PriceSnapshot(rates=rates, fingerprint=fingerprint(rates))


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


__all__ = ["PER_MILLION", "PriceSnapshot", "drift", "fingerprint", "pinned",
           "rates_for", "snapshot"]
