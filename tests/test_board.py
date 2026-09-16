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
