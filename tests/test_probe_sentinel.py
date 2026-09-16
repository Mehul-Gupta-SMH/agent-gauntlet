"""The instrument check, run live (#29).

Experiment 003's sentinel was degraded by a prompt. Offline a scripted
policy had no choice but to comply, so it ranked last and the instrument
looked sound; live, a capable model ignored the instruction and the
sentinel ranked 6th of 9, which voided the whole board including its gate
verdict.

The structural sentinel that replaced it had, until now, only ever been
validated offline -- in the one environment that cannot falsify it. These
cover the live check that closes that loop for the price of one call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_gauntlet import TaskSpec, architect
from agent_gauntlet import live as live_mod
from agent_gauntlet.cli import _probe, build_parser, hardest_scenario
from agent_gauntlet.offline import naive, verifying
from agent_gauntlet.score import Answer

AUDITED = Path(__file__).resolve().parents[1] / "fixtures" / "inventory" / "audited.yaml"
SCENARIO = hardest_scenario(TaskSpec.from_yaml(AUDITED))


def _run(monkeypatch, tmp_path, executor, *extra):
    monkeypatch.setattr(live_mod, "preflight", lambda *a, **k: None)
    monkeypatch.setattr(live_mod, "live_executor", lambda target: executor)
    return _probe(build_parser().parse_args(
        ["probe", str(AUDITED), "--out", str(tmp_path / "p"), *extra]
    ))


def _agent(policy, *, sentinel_answer=None):
    """A stub that routes through the interposer, like a real agent.

    `sentinel_answer` overrides what the sentinel reports, so the failure
    modes can be exercised without a model that actually misbehaves.
    """
    def execute(variant, seed):
        live_mod._final_text(object())
        if variant.is_sentinel and sentinel_answer is not None:
            return Answer(total=sentinel_answer)
        return policy()
    return execute


def test_the_probe_checks_the_sentinel_and_reports_the_shortfall(tmp_path, monkeypatch, capsys):
    code = _run(monkeypatch, tmp_path, _agent(verifying))
    out = capsys.readouterr().out

    assert code == 0
    assert "instrument check" in out
    assert "probing the sentinel" in out
    assert "the sentinel undercounts by" in out
    assert "The matrix is safe to run" in out


def test_a_sentinel_that_gets_the_right_answer_fails_the_probe(tmp_path, monkeypatch, capsys):
    """The failure this exists for.

    If a real model recovers the truth from a truncated record list, the
    degradation is not structural, the board cannot rank it last, and a
    matrix is void before it starts -- exactly what happened in 003, now
    caught for one call instead of a whole matrix.
    """
    code = _run(monkeypatch, tmp_path,
                _agent(verifying, sentinel_answer=SCENARIO.expected_total))
    out = capsys.readouterr().out

    assert code == 1
    assert "THE SENTINEL GOT THE RIGHT ANSWER" in out
    assert "void before it" in out


def test_an_unanswered_sentinel_fails_too(tmp_path, monkeypatch, capsys):
    """It must fail on quality, not by producing nothing (#29, risk 4).

    An unanswered sentinel exercises a different scoring path than a real
    variant, so it says nothing about whether the board can rank it last --
    and 'not correct' would otherwise let it pass.
    """
    def unanswering(variant, seed):
        live_mod._final_text(object())
        if variant.is_sentinel:
            return Answer(total=None)
        return verifying()

    code = _run(monkeypatch, tmp_path, unanswering)
    out = capsys.readouterr().out

    assert code == 1
    assert "the sentinel answered nothing" in out


def test_the_sentinel_cannot_reach_the_full_record_list(tmp_path, monkeypatch, capsys):
    """The degradation is a missing capability, and the probe shows it."""
    _run(monkeypatch, tmp_path, _agent(verifying))
    out = capsys.readouterr().out

    granted = architect.TOOLSETS[architect.SENTINEL_TOOLSET]
    assert "list_records_sample" in granted and "list_records" not in granted
    assert "'list_records_sample'" in out
    assert "granted tools" in out


def test_the_probe_variant_is_never_the_sentinel(tmp_path, monkeypatch, capsys):
    """Both sit on the cheap level. Probing the sentinel by accident would
    test the instrument and report it as the path."""
    seen = []

    def record(variant, seed):
        live_mod._final_text(object())
        seen.append(variant)
        return verifying()

    _run(monkeypatch, tmp_path, record)
    clean_half = seen[0]
    assert not clean_half.is_sentinel
    assert clean_half.factors["prompt"] == "verifying"
    assert any(v.is_sentinel for v in seen), "the sentinel must still be probed"


def test_no_sentinel_skips_the_check_and_says_so(tmp_path, monkeypatch, capsys):
    code = _run(monkeypatch, tmp_path, _agent(verifying), "--no-sentinel")
    out = capsys.readouterr().out

    assert code == 0
    assert "skipping the instrument check" in out
    assert "the sentinel undercounts" not in out


def test_an_outage_on_the_sentinel_is_not_a_build_failure(tmp_path, monkeypatch):
    def fails_on_sentinel(variant, seed):
        live_mod._final_text(object())
        if variant.is_sentinel:
            raise RuntimeError("overloaded_error: the model is overloaded")
        return verifying()

    assert _run(monkeypatch, tmp_path, fails_on_sentinel) == 4


def test_a_missing_dependency_on_the_sentinel_is_ours(tmp_path, monkeypatch):
    def breaks_on_sentinel(variant, seed):
        live_mod._final_text(object())
        if variant.is_sentinel:
            raise ModuleNotFoundError("No module named 'langchain_core'")
        return verifying()

    assert _run(monkeypatch, tmp_path, breaks_on_sentinel) == 1


def test_the_horizon_the_probe_prints_matches_what_the_sentinel_sees(tmp_path, monkeypatch, capsys):
    """A false diagnostic about the check that exists to catch false results.

    The first version printed the AUDIT's coverage as the sentinel's
    enumeration horizon -- "at most 2 of 6" for a sentinel that could see 3
    -- so the reassuring number in the log was simply wrong.
    """
    from agent_gauntlet.interpose import partial_horizon

    _run(monkeypatch, tmp_path, _agent(verifying))
    out = capsys.readouterr().out

    horizon = partial_horizon(len(SCENARIO.records))
    assert horizon != len(SCENARIO.audited_ids), (
        "this fixture no longer distinguishes the two numbers, so the test "
        "cannot catch the confusion it was written for"
    )
    assert f"at most {horizon} of {len(SCENARIO.records)} records" in out


def test_the_horizon_is_not_restated_anywhere():
    """One source of truth, so the message cannot drift from the behaviour."""
    from agent_gauntlet.interpose import list_record_ids_partial, partial_horizon
    import inspect

    body = inspect.getsource(list_record_ids_partial)
    assert "partial_horizon(" in body
    assert "// 2" not in body, "the truncation rule is stated in one place only"
    assert partial_horizon(6) == 3 and partial_horizon(1) == 1
