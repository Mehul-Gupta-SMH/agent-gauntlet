"""Board behaviour: aggregation, gating, ties, attribution, export."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_gauntlet import Ledger, TaskSpec, architect, board, run_matrix

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "inventory" / "task.yaml"
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
            ["commonadk", "validate", v.common_dir], capture_output=True, text=True
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
    naive = [r for r in results if r.factors.get("prompt") == "naive"]
    assert naive, "no naive variants generated"
    for r in naive:
        assert r.propagation_rate == 1.0, (
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


def test_verifying_variants_are_not_gated(run):
    _, _, results, _ = run
    good = [r for r in results if r.factors.get("prompt") == "verifying"]
    assert good and not any(r.gated for r in good)


# --- ranking, attribution, export ----------------------------------------


def test_rank_returns_tie_tiers(run):
    _, _, results, _ = run
    tiers = board.rank(results)
    assert sum(len(t) for t in tiers) == len(results)
    qualities = [t[0].quality for t in tiers]
    assert qualities == sorted(qualities, reverse=True)


def test_no_winner_when_top_tier_is_tied(run):
    """Refusing to break a tie is the honest answer, not a gap."""
    _, _, results, _ = run
    top = board.rank(results)[0]
    if len(top) > 1:
        assert board.winner(results) is None


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
    """A deliberately broken config would drag down every level it sits in."""
    _, _, results, _ = run
    effects = {e.factor: e for e in board.factor_effects(results)}
    assert "sentinel" not in effects["prompt"].levels


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
        ["commonadk", "validate", str(dest)], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_export_refuses_a_variant_with_no_project(run, tmp_path):
    from agent_gauntlet.spec import VariantSpec

    _, _, results, _ = run
    with pytest.raises(ValueError, match="no common/ folder"):
        board.export_winner(results[0], [VariantSpec(id=results[0].variant_id)],
                            tmp_path / "x")
