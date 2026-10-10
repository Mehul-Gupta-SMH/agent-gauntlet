"""Pinning the rates a dollar figure was computed against (#14).

`commonadk.runners.pricing` says of itself: *"This is a snapshot, not a
live price feed [...] It WILL drift out of date."* Nothing on this side
recorded which snapshot a run used, so two matrices run a month apart
averaged into one cost column with no way to tell the rates had moved.

The tests below are mostly about the third part of #14's smallest
defensible position -- the one with teeth. A variant priced two different
ways reports no cost per run at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_gauntlet import Ledger, TaskSpec, architect, board, pricing, run_matrix
from agent_gauntlet.score import Answer

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "inventory" / "audited.yaml"
MODELS = {"cheap": "anthropic/claude-haiku-4-5", "smart": "anthropic/claude-sonnet-5"}


@pytest.fixture
def task() -> TaskSpec:
    return TaskSpec.from_yaml(FIXTURE)


@pytest.fixture
def grid(task, tmp_path):
    """Real variants: `VariantSpec.model` carries the resolved model string,
    and that string is what the rates are looked up by."""
    return architect.generate(out_dir=tmp_path / "v", task=task, models=MODELS,
                              prompts=["verifying"],
                              toolsets=["records+summary"],
                              include_sentinel=False)


def _metered(cost=0.001):
    """An executor that meters, so the run looks live to the matrix."""
    def execute(variant, seed):
        # The shape the board reads: commonadk nests the meter under
        # `llm_calls`, and a fake that flattened it would test nothing.
        return (Answer(total=0, steps=1),
                {"llm_calls": {"cost_usd": cost, "total_tokens": 100,
                               "cost_complete": True}})
    return execute


# --- reading the rates ----------------------------------------------------


def test_the_rates_are_read_through_the_public_function():
    """Probed, not scraped. `estimate_cost_usd(model, 1M, 0)` IS the input
    rate per million by construction, so this keeps working if upstream
    renames its table -- and records what the pricing actually did rather
    than what a private dict was hoped to contain.
    """
    from commonadk.runners.pricing import estimate_cost_usd

    rates = pricing.rates_for(["anthropic/claude-haiku-4-5"])
    got = rates["anthropic/claude-haiku-4-5"]
    assert got is not None
    expected_in = estimate_cost_usd("claude-haiku-4-5", pricing.PER_MILLION, 0)
    assert got[0] == pytest.approx(expected_in)


def test_an_unpriced_model_records_none_not_zero():
    """"The table did not price this model" and "this model was free" must
    not render identically -- the same rule as every other censored value
    here, applied to the rate itself."""
    rates = pricing.rates_for(["nobody/invented-this"])
    assert rates["nobody/invented-this"] is None


def test_the_fingerprint_moves_when_a_rate_moves():
    before = pricing.fingerprint({"m": [1.0, 5.0]})
    same = pricing.fingerprint({"m": [1.0, 5.0]})
    after = pricing.fingerprint({"m": [1.0, 5.01]})
    assert before == same
    assert before != after


def test_an_unpriced_model_is_distinguishable_from_a_free_one():
    assert pricing.fingerprint({"m": None}) != pricing.fingerprint({"m": [0.0, 0.0]})


# --- what gets pinned -----------------------------------------------------


def test_an_offline_run_pins_nothing(task, grid, tmp_path):
    """It spends nothing and is priced by nobody, so an absent fingerprint
    must not read as drift."""
    records = run_matrix(task=task, variants=grid,
                         ledger=Ledger(tmp_path / "off.jsonl"),
                         base_seed="s", repeats=1)
    assert all(not r.prices for r in records)
    assert pricing.drift(records) == {}


def test_a_metered_run_pins_the_rates_it_used(task, grid, tmp_path):
    records = run_matrix(task=task, variants=grid,
                         ledger=Ledger(tmp_path / "live.jsonl"),
                         base_seed="s", repeats=1,
                         executor=_metered(), offline=False)
    assert records
    fingerprints = {pricing.pinned(r) for r in records}
    assert len(fingerprints) == 1 and None not in fingerprints
    rates = records[0].prices["rates"]
    assert set(rates) == set(MODELS.values())


# --- the part with teeth --------------------------------------------------


def test_one_rate_table_is_not_drift(task, grid, tmp_path):
    records = run_matrix(task=task, variants=grid,
                         ledger=Ledger(tmp_path / "a.jsonl"),
                         base_seed="s", repeats=1,
                         executor=_metered(), offline=False)
    assert len(pricing.drift(records)) == 1
    rows = board.summarize(records)
    assert all(not r.prices_mixed for r in rows)
    assert all(r.cost_per_run is not None for r in rows)


def test_a_variant_priced_two_ways_reports_no_cost_per_run(task, grid, tmp_path):
    """The load-bearing one. Averaging dollars across two rate tables
    produces a figure computed from numbers that were never simultaneously
    true, and it used to print with the same confidence as any other."""
    records = run_matrix(task=task, variants=grid,
                         ledger=Ledger(tmp_path / "b.jsonl"),
                         base_seed="s", repeats=1,
                         executor=_metered(), offline=False)
    # The provider changed its prices halfway through the ledger.
    moved = [
        r.model_copy(update={"prices": {**r.prices, "fingerprint": "deadbeef"}})
        if i % 2 else r
        for i, r in enumerate(records)
    ]
    assert len(pricing.drift(moved)) == 2

    rows = board.summarize(moved)
    assert all(r.prices_mixed for r in rows)
    assert all(r.cost_per_run is None for r in rows), (
        "a cost averaged over two rate tables must render n/a"
    )
    # The totals are still recorded -- the spend happened.
    assert all(r.cost_usd > 0 for r in rows)


def test_the_censoring_reaches_the_budget_pick(task, grid, tmp_path):
    """Gated on the property rather than at the print, so `best_under` and
    the frontier censor it too without knowing prices can drift."""
    records = run_matrix(task=task, variants=grid,
                         ledger=Ledger(tmp_path / "c.jsonl"),
                         base_seed="s", repeats=1,
                         executor=_metered(), offline=False)
    moved = [r.model_copy(update={"prices": {**r.prices,
                                             "fingerprint": "deadbeef"}})
             if i % 2 else r for i, r in enumerate(records)]

    chosen = board.best_under(board.summarize(moved), 1.00)
    pick, unpriced = chosen.pick, chosen.unpriced
    assert pick is None, "a pick under a ceiling needs a comparable price"
    assert unpriced, "and the rows are handed back rather than dropped"


# --- the other confound the ledger could not see (#9) ---------------------


def test_the_target_is_recorded_on_a_live_run(task, grid, tmp_path):
    """`model` was pinned per run and the framework was not, so two live
    matrices against different SDKs appended to one ledger and averaged
    into one column. Every number this board prints is scoped to a
    framework, and until now nothing said which."""
    from agent_gauntlet.ledger import target_drift

    records = run_matrix(task=task, variants=grid,
                         ledger=Ledger(tmp_path / "t.jsonl"),
                         base_seed="s", repeats=1,
                         executor=_metered(), offline=False, target="langgraph")
    assert {r.target for r in records} == {"langgraph"}
    assert set(target_drift(records)) == {"langgraph"}


def test_an_offline_run_has_no_target(task, grid, tmp_path):
    """No framework was involved, so an absent target is not drift -- the
    same rule as the absent price table."""
    from agent_gauntlet.ledger import target_drift

    records = run_matrix(task=task, variants=grid,
                         ledger=Ledger(tmp_path / "o.jsonl"),
                         base_seed="s", repeats=1)
    assert all(r.target is None for r in records)
    assert target_drift(records) == {}


def test_two_frameworks_in_one_ledger_are_visible(task, grid, tmp_path):
    """The confound a framework axis is supposed to resolve rather than
    contain. It cannot be resolved while it is invisible."""
    from agent_gauntlet.ledger import target_drift

    a = run_matrix(task=task, variants=grid,
                   ledger=Ledger(tmp_path / "a.jsonl"), base_seed="s",
                   repeats=1, executor=_metered(), offline=False,
                   target="langgraph")
    b = run_matrix(task=task, variants=grid,
                   ledger=Ledger(tmp_path / "b.jsonl"), base_seed="s",
                   repeats=1, executor=_metered(), offline=False,
                   target="crewai")
    seen = target_drift([*a, *b])
    assert set(seen) == {"crewai", "langgraph"}
    assert all(seen[k] for k in seen)


def test_the_claude_target_is_not_priced_by_the_rate_table():
    """The pin was a claim about a table that produced none of the dollars (#47).

    Asserted against commonadk's own source, not against my reading of it:
    the Claude Agent SDK runner takes `cost_usd` from
    `ResultMessage.total_cost_usd` and says in its docstring that
    `runners/pricing.py` is "never consulted for this target". `--target
    claude` is this project's default and the only target it has ever run
    live, so every live dollar in every ledger came from the SDK's figure
    while every record pinned a rate table instead.
    """
    import inspect

    from commonadk.runners import claude_agent

    source = inspect.getsource(claude_agent)
    assert "total_cost_usd" in source
    # Stops before the line wrap in upstream's docstring, deliberately --
    # the sentence continues "for this target" on the next line.
    assert "is never consulted for this" in source, (
        "upstream changed its cost attribution -- pricing.AUTHORITY needs "
        "re-checking before this test is relaxed"
    )

    snap = pricing.snapshot(["anthropic/claude-haiku-4-5"], target="claude")
    assert snap.source == pricing.SDK_PRICED
    assert snap.rates == {}, "there is no table to pin, so none is pinned"
    assert not snap.detects_drift, "the SDK publishes no rates to compare"
    assert snap.models_cache_and_batch_tiers


def test_a_table_priced_target_still_pins_the_table():
    snap = pricing.snapshot(["anthropic/claude-haiku-4-5"], target="langgraph")
    assert snap.source == pricing.TABLE
    assert snap.rates == {"anthropic/claude-haiku-4-5": [1.0, 5.0]}
    assert snap.detects_drift
    assert not snap.models_cache_and_batch_tiers, (
        "flat input/output rates: no cache tier, no batch tier (#47)"
    )


def test_adding_the_authority_moved_no_table_fingerprint():
    """A new field joins the payload only when it is not the default.

    The same rule as `TaskSpec.fingerprint`, for the same reason: rehashing
    every table-priced snapshot would detach the pins already in ledgers.
    """
    models = ["anthropic/claude-haiku-4-5", "anthropic/claude-sonnet-5"]
    rates = pricing.rates_for(models)
    assert pricing.fingerprint(rates) == "06e68075941ee991"
    assert pricing.fingerprint(rates, source=pricing.TABLE) == "06e68075941ee991"


def test_each_authority_gets_its_own_fingerprint():
    """So a ledger mixing them reads n/a rather than averaging.

    Dollars from a flat table and dollars the SDK billed are not the same
    measurement, which is exactly what `prices_mixed` is for.
    """
    models = ["anthropic/claude-haiku-4-5"]
    fps = {
        pricing.snapshot(models, target=t).fingerprint
        for t in ("claude", "langgraph", "mystery_sdk")
    }
    assert len(fps) == 3


def test_an_unlisted_target_is_unrecorded_not_assumed():
    """Assuming the table is how the wrong authority got pinned."""
    snap = pricing.snapshot(["anthropic/claude-haiku-4-5"], target="something_new")
    assert snap.source == pricing.UNKNOWN
    assert not snap.detects_drift
    assert not snap.models_cache_and_batch_tiers
    assert pricing.authority_for(None) == pricing.UNKNOWN


# --- the mixed authority, which used to read as a missing one -------------


def test_mixed_authority_is_its_own_answer_not_unrecorded():
    """Two situations, one return value, and the message named the wrong
    one (#47, experiment 015).

    `UNKNOWN` means no row recorded an authority. `MIXED` means several did
    and they disagree -- measured at 1.83x-2.29x over five live probes, so
    a cost column spanning both is off by about a factor of two on some
    rows, not approximate.
    """
    from agent_gauntlet import pricing
    from agent_gauntlet.cli import _price_authority

    class _R:
        def __init__(self, source):
            self.prices = {"source": source} if source else {}

    assert _price_authority([]) == pricing.UNKNOWN
    assert _price_authority([_R(None), _R(None)]) == pricing.UNKNOWN
    assert _price_authority([_R(pricing.TABLE)]) == pricing.TABLE
    assert _price_authority([_R(pricing.SDK_PRICED)]) == pricing.SDK_PRICED
    assert (_price_authority([_R(pricing.TABLE), _R(pricing.SDK_PRICED)])
            == pricing.MIXED)
    assert pricing.MIXED != pricing.UNKNOWN


def test_the_board_says_a_mixed_cost_column_is_not_comparable():
    """The warning has to name the consequence, not just the condition."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src" / "agent_gauntlet"
              / "cli.py").read_text(encoding="utf-8")
    branch = source.split("pricing.MIXED:", 1)[1].split("else:", 1)[0]
    assert "not comparable" in branch
    assert "factor of two" in branch
    assert "Split the ledger by target" in branch, (
        "a caveat without an action is a caveat nobody applies")
