"""Board behaviour: aggregation, gating, ties, attribution, export."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from agent_gauntlet import Ledger, TaskSpec, architect, board, run_matrix

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "inventory" / "task.yaml"
COMMONADK = str(Path(sys.executable).parent / "commonadk")
"""Resolved next to the running interpreter rather than trusting PATH, so
the suite validates with the same commonadk the package is installed against."""
MODELS = {"cheap": "openai/gpt-4o-mini", "smart": "anthropic/claude-sonnet-5"}


@pytest.fixture
def task() -> TaskSpec:
    return TaskSpec.from_yaml(FIXTURE)


@pytest.fixture
def run(task, tmp_path):
    variants = architect.generate(out_dir=tmp_path / "variants", task=task, models=MODELS)
    ledger = Ledger(tmp_path / "runs.jsonl")
    run_matrix(task=task, variants=variants, ledger=ledger, base_seed="s", repeats=2)
    results = board.summarize(ledger.records())
    sentinels = {v.id for v in variants if v.is_sentinel}
    for r in results:
        r.is_sentinel = r.variant_id in sentinels
    return task, variants, results, tmp_path


# --- generation -----------------------------------------------------------


def test_generated_projects_pass_commonadk_validate(task, tmp_path):
    """The integration that makes a variant a real, runnable project."""
    variants = architect.generate(out_dir=tmp_path / "v", task=task, models=MODELS)
    assert len(variants) == 9, "2 models x 2 prompts x 2 toolsets + sentinel"
    for v in variants:
        proc = subprocess.run(
            [COMMONADK, "validate", v.common_dir], capture_output=True, text=True
        )
        assert proc.returncode == 0, f"{v.id}:\n{proc.stdout}{proc.stderr}"


def test_every_variant_gets_the_same_task_statement(task, tmp_path):
    """A fairness invariant -- the statement must not vary with the factors."""
    variants = architect.generate(out_dir=tmp_path / "v", task=task, models=MODELS)
    for v in variants:
        skill = (Path(v.common_dir) / "auditor" / "skill.md").read_text()
        assert task.statement.strip() in skill


def test_generated_tools_route_through_the_interposer(task, tmp_path):
    """Fault injection must sit below the adapter, not in a hook."""
    variants = architect.generate(out_dir=tmp_path / "v", task=task, models=MODELS)
    tools = (Path(variants[0].common_dir) / "auditor" / "tools.py").read_text()
    assert "from agent_gauntlet import interpose" in tools
    assert "interpose.fetch_quantity" in tools


def test_unknown_factor_level_rejected(task, tmp_path):
    with pytest.raises(KeyError, match="unknown prompt strategy"):
        architect.generate(
            out_dir=tmp_path / "v", task=task, models=MODELS, prompts=["nope"]
        )


# --- the propagation gate -------------------------------------------------


def test_propagation_counted_over_all_faulted_runs(run):
    """The regression that matters.

    Scoped to evidence-reachable runs, a variant that propagates *and*
    cannot detect reads 0% and escapes the gate -- the single most
    dangerous configuration silently rated safest. Propagation is
    observable whether or not a cross-check existed.
    """
    _, _, results, _ = run
    # The sentinel also carries prompt=naive -- its degradation is in the tool
    # set now -- but it undercounts far past the credulous figure, so it is
    # genuinely not propagating and must not be asserted about here.
    naive = [
        r for r in results
        if r.factors.get("prompt") == "naive" and not r.is_sentinel
    ]
    assert naive, "no naive variants generated"
    for r in naive:
        assert r.propagation_rate == 1.0, (  # over decidable runs only
            f"{r.variant_id} propagates every faulted run but reports "
            f"{r.propagation_rate:.0%}"
        )
        assert r.gated


def test_variant_without_cross_check_has_no_detection_rate(run):
    """The genuine censoring case: not measured, not zero."""
    _, _, results, _ = run
    blind = [r for r in results if r.factors.get("toolset") == "records"]
    assert blind
    assert all(r.detection_rate is None for r in blind)


def test_prompt_and_toolset_interact(run):
    """A verifying prompt cannot save an agent with nothing to verify.

    With the tool grant enforced, `verifying` + `records` degrades to the
    credulous path and is gated, while `verifying` + `records+summary`
    detects and survives. The prompt factor's effect therefore *depends on*
    the toolset level -- a real interaction, and a concrete demonstration
    that one-at-a-time marginal effects misreport this grid (#22).
    """
    _, _, results, _ = run
    verifying = {r.factors.get("toolset"): r for r in results
                 if r.factors.get("prompt") == "verifying"}
    assert verifying["records+summary"].gated is False
    assert verifying["records"].gated is True


def test_tool_grant_is_enforced_not_declared(run):
    """A variant without the cross-check must be unable to reach it."""
    _, _, results, _ = run
    blind = [r for r in results if r.factors.get("toolset") == "records"]
    assert blind
    assert all(r.detection_rate is None for r in blind), (
        "a variant with no granted cross-check cannot have a detection rate"
    )


# --- ranking, attribution, export ----------------------------------------


def test_rank_returns_tie_tiers(run):
    _, _, results, _ = run
    tiers = board.rank(results)
    assert sum(len(t) for t in tiers) == len(results)
    qualities = [t[0].accuracy for t in tiers]
    assert qualities == sorted(qualities, reverse=True)


def test_winner_is_unique_non_dominated_not_top_of_ranking(run):
    """Domination, not rank position.

    Two configs can tie on accuracy and differ sharply on how often they
    cry wolf; ranking on one axis discards that. The winner must be
    non-dominated across the objectives, ungated, and unique.
    """
    _, _, results, _ = run
    champion = board.winner(results)
    if champion is None:
        return  # a genuine tie -- covered below
    assert not champion.gated and not champion.is_sentinel
    for other in results:
        if other is champion or other.gated or other.is_sentinel:
            continue
        dominates = (
            other.accuracy >= champion.accuracy
            and other.false_alarm_rate <= champion.false_alarm_rate
            and (other.accuracy > champion.accuracy
                 or other.false_alarm_rate < champion.false_alarm_rate)
        )
        assert not dominates, f"{other.variant_id} dominates the declared winner"


def test_no_winner_when_two_configs_are_genuinely_tied():
    """Refusing to break a real tie is the honest answer, not a gap."""
    from agent_gauntlet.board import VariantResult

    tied = [
        VariantResult(
            variant_id=f"v{i}", n_runs=2, quality=1.0, accuracy=1.0,
            clean_quality=1.0, faulted_quality=1.0, propagation_rate=0.0,
            detection_rate=1.0, false_alarm_rate=0.1, cost_usd=0.0, mean_steps=3.0,
        )
        for i in range(2)
    ]
    assert board.winner(tied) is None


def test_gated_config_cannot_win():
    """Propagation is a gate: no score redeems a config that passed on a lie."""
    from agent_gauntlet.board import VariantResult

    best_but_gated = VariantResult(
        variant_id="gated", n_runs=2, quality=1.0, accuracy=1.0,
        clean_quality=1.0, faulted_quality=1.0, propagation_rate=0.5,
        detection_rate=0.0, false_alarm_rate=0.0, cost_usd=0.0, mean_steps=3.0,
    )
    worse_but_clean = VariantResult(
        variant_id="clean", n_runs=2, quality=0.5, accuracy=0.6,
        clean_quality=0.5, faulted_quality=0.5, propagation_rate=0.0,
        detection_rate=1.0, false_alarm_rate=0.0, cost_usd=0.0, mean_steps=3.0,
    )
    assert board.winner([best_but_gated, worse_but_clean]).variant_id == "clean"


def test_sentinel_ranks_last(run):
    _, _, results, _ = run
    assert board.rank(results)[-1][0].is_sentinel


def test_prompt_is_the_dominant_factor(run):
    """The positive control, read off the attribution table."""
    _, _, results, _ = run
    effects = {e.factor: e for e in board.factor_effects(results)}
    assert effects["prompt"].spread > 0
    assert effects["prompt"].levels["verifying"] > effects["prompt"].levels["naive"]


def test_factor_effects_carry_their_caveat(run):
    """The interaction caveat must not be separable from the numbers."""
    _, _, results, _ = run
    for e in board.factor_effects(results):
        assert "interaction" in e.caveat and "#22" in e.caveat


def test_sentinel_excluded_from_attribution(run):
    """A deliberately broken config would drag down every level it sits in.

    Sharper now than when the sentinel had a prompt level of its own: it
    shares `prompt=naive` with real variants, so exclusion has to happen on
    the *variant*, not by the level name never colliding.
    """
    _, _, results, _ = run
    effects = {e.factor: e for e in board.factor_effects(results)}
    assert architect.SENTINEL_TOOLSET not in effects["toolset"].levels

    sentinel = next(r for r in results if r.is_sentinel)
    assert effects["prompt"].levels["naive"] > sentinel.quality, (
        "the sentinel's quality leaked into the prompt level it shares"
    )


def test_sentinel_is_degraded_structurally_not_by_prompt(run):
    """The defect from experiment 003, pinned at the variant level.

    A prompt asking a capable model to be careless is not a degradation --
    the model ignores it. The sentinel must differ from a real variant by a
    capability it does not have.
    """
    _, variants, _, _ = run
    sentinel = next(v for v in variants if v.is_sentinel)

    assert sentinel.factors["prompt"] != "sentinel", "no attitudinal sentinel"
    assert sentinel.factors["toolset"] == architect.SENTINEL_TOOLSET

    granted = architect.TOOLSETS[sentinel.factors["toolset"]]
    assert "list_records" not in granted, "it must not be able to enumerate fully"
    assert "get_summary" not in granted, "nor to reach the cross-check"

    prompt = (Path(sentinel.common_dir) / "auditor" / "skill.md").read_text()
    peers = [
        v for v in variants
        if not v.is_sentinel and v.factors["prompt"] == sentinel.factors["prompt"]
    ]
    assert peers, "no real variant shares the sentinel's prompt"
    for real in peers:
        assert prompt == (Path(real.common_dir) / "auditor" / "skill.md").read_text(), (
            "the sentinel must be told exactly what a real variant is told"
        )


def test_pareto_front_is_non_dominated(run):
    _, _, results, _ = run
    front = board.pareto(results, objectives=("quality", "mean_steps"),
                         maximize=(True, False))
    assert front
    for r in front:
        assert not any(
            o.quality >= r.quality and o.mean_steps <= r.mean_steps
            and (o.quality > r.quality or o.mean_steps < r.mean_steps)
            for o in results if o is not r
        )


def test_export_produces_a_runnable_project(run, tmp_path):
    """The deliverable is a project, not a score."""
    _, variants, results, _ = run
    best = next(r for r in results if r.factors.get("prompt") == "verifying")
    dest = board.export_winner(best, variants, tmp_path / "winner")
    assert (dest / "config.yaml").is_file()
    proc = subprocess.run(
        [COMMONADK, "validate", str(dest)], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_export_refuses_a_variant_with_no_project(run, tmp_path):
    from agent_gauntlet.spec import VariantSpec

    _, _, results, _ = run
    with pytest.raises(ValueError, match="no common/ folder"):
        board.export_winner(results[0], [VariantSpec(id=results[0].variant_id)],
                            tmp_path / "x")


# --- from an ordering to a decision you can defend (#36) ------------------


def _v(vid, quality, *, low, high, cost=None, priced=True, n=20, sentinel=False):
    """A board row with a chosen quality and interval."""
    from agent_gauntlet.stats import Interval

    return board.VariantResult(
        variant_id=vid, factors={}, n_runs=n, quality=quality, accuracy=quality,
        clean_quality=quality, faulted_quality=quality, propagation_rate=0.0,
        detection_rate=None, false_alarm_rate=0.0, mean_steps=1.0,
        cost_usd=(cost or 0.0) * n, cost_complete=priced, is_sentinel=sentinel,
        intervals={"quality": Interval(value=quality, low=low, high=high,
                                       n=n, method="wilson")},
    )


def test_a_tier_is_what_the_run_could_not_separate():
    """The held-out split polices the winner; people read the whole column.
    An ordering this run cannot support below the top is still an ordering
    somebody will act on."""
    rows = [
        _v("top", 0.95, low=0.85, high=0.99),
        _v("near", 0.90, low=0.80, high=0.96),   # overlaps `top`
        _v("far", 0.40, low=0.25, high=0.55),    # does not
    ]
    tiers = board.evidence_tiers(rows)
    assert [[r.variant_id for r in t] for t in tiers] == [["top", "near"], ["far"]]


def test_a_tier_never_claims_they_are_equal():
    """Non-overlap implies a real difference; overlap does not imply
    sameness. The comparison is against the tier's best, which is the
    conservative direction -- it merges more, and claims less."""
    rows = [_v("a", 0.90, low=0.80, high=0.96),
            _v("b", 0.88, low=0.78, high=0.94)]
    tier = board.evidence_tiers(rows)[0]
    assert len(tier) == 2
    assert tier[0].quality != tier[1].quality, "the values still differ"


def test_decidable_depth_counts_only_the_rows_the_run_ordered():
    """Three bands -- one alone, one alone, then three that overlap. The
    order holds to row 2 and no further (#44)."""
    rows = [
        _v("first", 0.95, low=0.92, high=0.98),
        _v("second", 0.80, low=0.76, high=0.84),
        _v("third", 0.50, low=0.44, high=0.56),
        _v("fourth", 0.49, low=0.43, high=0.55),
        _v("fifth", 0.48, low=0.42, high=0.54),
    ]
    assert board.decidable_depth(rows) == 2
    assert board.rank_labels(rows) == {
        "first": "1", "second": "2",
        "third": "3=", "fourth": "3=", "fifth": "3=",
    }


def test_depth_zero_is_a_real_answer_not_a_missing_one():
    """A board whose top two overlap ordered nothing at all. That is
    different from a board with nothing to rank, and the two must not
    arrive as the same value."""
    rows = [_v("a", 0.90, low=0.70, high=0.99),
            _v("b", 0.88, low=0.68, high=0.98)]
    assert board.decidable_depth(rows) == 0
    assert board.rank_labels(rows) == {"a": "1=", "b": "1="}
    assert board.decidable_depth([]) is None


def test_rank_labels_number_by_competition_not_by_band():
    """Three rows tied at 3 are followed by 6, not by 4. The label has to
    say where the band sits in the field, not which band it is."""
    rows = [
        _v("a", 0.95, low=0.93, high=0.97),
        _v("b", 0.60, low=0.55, high=0.65),
        _v("c", 0.59, low=0.54, high=0.64),
        _v("d", 0.58, low=0.53, high=0.63),
        _v("e", 0.20, low=0.15, high=0.25),
    ]
    labels = board.rank_labels(rows)
    assert labels["a"] == "1"
    assert labels["b"] == labels["c"] == labels["d"] == "2="
    assert labels["e"] == "5"


def test_a_sentinel_gets_no_rank_at_all():
    """It is built to lose. Giving it a position would put a deliberately
    broken config into a ranking of contenders."""
    rows = [_v("real", 0.9, low=0.85, high=0.95),
            _v("sent", 0.1, low=0.05, high=0.15, sentinel=True)]
    assert "sent" not in board.rank_labels(rows)
    assert board.decidable_depth(rows) == 1


def test_depth_from_one_run_agrees_with_the_seed_sweep(tmp_path):
    """The cheap version of experiment 009's answer, cross-checked.

    009 swept k across eight seeds, 177,408 runs, and found depths 1 and 2
    lock in at k=4 and never move while depth 3 never settles at any
    budget. This computes the same boundary from a SINGLE run's intervals,
    which is the only version an operator can act on. They have to agree,
    or the cheap number is not measuring the thing the expensive one did.
    """
    from agent_gauntlet import VariantSpec

    audited = FIXTURE.parent / "audited.yaml"
    task = TaskSpec.from_yaml(audited)
    grid = [
        VariantSpec(id=f"{m}-{p}-{t}",
                    factors={"model": m, "prompt": p, "toolset": t})
        for m in ("cheap", "smart") for p in ("naive", "verifying")
        for t in ("records", "records+summary")
    ]
    ledger = Ledger(tmp_path / "runs.jsonl")
    run_matrix(task=task, variants=grid, ledger=ledger, base_seed="seed-a",
               repeats=30)
    results = board.summarize(ledger.records())

    for metric in ("accuracy", "quality"):
        assert board.decidable_depth(results, metric=metric) == 2, metric

    # And the two rows it does order are the two 009 found stable.
    labels = board.rank_labels(results, metric="quality")
    top = {v for v, lab in labels.items() if lab in ("1", "2")}
    assert top == {"smart-verifying-records+summary",
                   "cheap-verifying-records+summary"}


def test_a_row_with_no_interval_is_left_out_rather_than_ranked():
    """It cannot be placed against one, and quietly ranking it would be an
    ordering built on a comparison nobody made."""
    naked = _v("naked", 0.99, low=0, high=1)
    naked.intervals = {}
    tiers = board.evidence_tiers([naked, _v("a", 0.5, low=0.3, high=0.7)])
    assert [r.variant_id for t in tiers for r in t] == ["a"]


def test_the_best_config_under_a_ceiling():
    """The question an operator actually arrives with: a constraint, not a
    preference."""
    rows = [
        _v("dear", 0.95, low=0.85, high=0.99, cost=0.50),
        _v("fine", 0.93, low=0.83, high=0.98, cost=0.02),
        _v("cheap_bad", 0.30, low=0.18, high=0.45, cost=0.001),
    ]
    pick, unpriced = board.best_under(rows, 0.10)
    assert pick.variant_id == "fine"
    assert unpriced == []


def test_inside_the_top_tier_the_tiebreak_is_the_constraint():
    """Picking between configs the run could not tell apart on quality
    would be reading noise, so it breaks on the thing the operator
    actually stated."""
    rows = [
        _v("pricey", 0.95, low=0.85, high=0.99, cost=0.09),
        _v("thrifty", 0.92, low=0.82, high=0.97, cost=0.01),
    ]
    pick, _ = board.best_under(rows, 0.10)
    assert pick.variant_id == "thrifty"


def test_an_unpriced_config_is_not_a_cheap_one():
    """#14, under a ceiling. Admitting them would make "no price" the
    cheapest possible answer, for the wrong reason -- and they are handed
    back rather than dropped, because the cheapest option might be there."""
    rows = [
        _v("priced", 0.80, low=0.70, high=0.90, cost=0.05),
        _v("mystery", 0.99, low=0.90, high=1.0, priced=False),
    ]
    pick, unpriced = board.best_under(rows, 1.00)
    assert pick.variant_id == "priced"
    assert [r.variant_id for r in unpriced] == ["mystery"]


def test_nothing_affordable_is_an_answer():
    rows = [_v("dear", 0.95, low=0.85, high=0.99, cost=5.0)]
    pick, _ = board.best_under(rows, 0.10)
    assert pick is None


def test_the_search_reports_its_own_cost():
    """Every contender's $/run was on the board; what finding the winner
    cost was nowhere, which makes the escalation ladder a claim rather
    than a budget."""
    rows = [_v("a", 0.9, low=0.8, high=0.95, cost=0.01, n=10),
            _v("b", 0.8, low=0.7, high=0.90, cost=0.02, n=10)]
    cost = board.search_cost(rows)
    assert cost["runs"] == 20 and cost["variants"] == 2
    assert cost["usd"] == pytest.approx(0.30)
    assert cost["complete"]


def test_one_unpriced_run_makes_the_search_cost_a_floor():
    rows = [_v("a", 0.9, low=0.8, high=0.95, cost=0.01, n=10),
            _v("b", 0.8, low=0.7, high=0.90, priced=False, n=10)]
    assert not board.search_cost(rows)["complete"]


def test_the_effects_table_says_when_not_to_trust_itself(task, tmp_path):
    """The failure #22 is about, detected cheaply. On this fixture the
    prompt axis is worth ~3 points with a bare tool set and ~47 with a
    cross-check -- the marginal number describes neither.
    """
    variants = architect.generate(out_dir=tmp_path / "v", task=task, models=MODELS)
    ledger = Ledger(tmp_path / "runs.jsonl")
    run_matrix(task=task, variants=variants, ledger=ledger, base_seed="s",
               repeats=3)
    results = board.summarize(ledger.records())
    for r in results:
        r.is_sentinel = any(v.id == r.variant_id and v.is_sentinel
                            for v in variants)

    effects = {e.factor: e for e in board.factor_effects(results)}
    prompt = effects["prompt"]
    assert len(prompt.conditional_spreads) >= 2
    assert prompt.interaction_range >= prompt.spread, (
        "the prompt axis swings more across tool sets than its own marginal "
        "number -- that is the caveat firing, not a flaky assertion"
    )


def test_a_single_cell_has_no_interaction_range():
    """Below two cells there is nothing to disagree, and 0.0 would read as
    'this axis is stable'."""
    effect = board.FactorEffect(factor="model", levels={"a": 0.5, "b": 0.7},
                                spread=0.2,
                                conditional_spreads={"prompt=naive": 0.2})
    assert effect.interaction_range is None
