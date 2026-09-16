"""The probe's faulted half.

Until this existed the probe only ever ran clean, so live fault injection
was unverified until the matrix spent on it. That is the expensive blind
spot: if a corrupted result never reaches the agent, every propagation and
detection number on the board is a confident zero and the board looks
immaculate. Nothing crashes.

The check is decidable without the model's cooperation -- either a tool
call returned a faulted result or it did not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_gauntlet import TaskSpec
from agent_gauntlet import live as live_mod
from agent_gauntlet.cli import _probe, build_parser, hardest_scenario
from agent_gauntlet.interpose import ToolTimeout
from agent_gauntlet.offline import naive, verifying
from agent_gauntlet.score import Answer

AUDITED = Path(__file__).resolve().parents[1] / "fixtures" / "inventory" / "audited.yaml"


def _run(monkeypatch, tmp_path, executor, *extra):
    monkeypatch.setattr(live_mod, "preflight", lambda *a, **k: None)
    monkeypatch.setattr(live_mod, "live_executor", lambda target: executor)
    return _probe(build_parser().parse_args(
        ["probe", str(AUDITED), "--out", str(tmp_path / "p"), *extra]
    ))


def _through_the_interposer(policy):
    """A stub agent that actually calls tools, like a real one does."""
    def execute(variant, seed):
        live_mod._final_text(object())
        return policy()
    return execute


def test_a_fault_that_never_reaches_the_agent_fails_the_probe(tmp_path, monkeypatch, capsys):
    """The failure mode worth $0.01 to catch.

    An agent that answers without calling a tool is never exposed to the
    corrupted value. A matrix of those scores propagation 0% and detection
    0% across the board -- immaculate, and meaningless.
    """
    def answers_without_looking(variant, seed):
        live_mod._final_text(object())
        return Answer(total=3635)

    code = _run(monkeypatch, tmp_path, answers_without_looking)
    out = capsys.readouterr().out

    assert code == 1
    assert "the fault was scheduled and no tool call returned it" in out
    assert "confident zero" in out


def test_a_credulous_agent_is_exposed_and_scored(tmp_path, monkeypatch, capsys):
    code = _run(monkeypatch, tmp_path, _through_the_interposer(naive))
    out = capsys.readouterr().out

    assert code == 0
    assert "faulted results reaching the agent: 1" in out
    assert "propagated=True" in out
    assert "The matrix is safe to run" in out


def test_a_verifying_agent_detects_and_the_probe_says_so(tmp_path, monkeypatch, capsys):
    code = _run(monkeypatch, tmp_path, _through_the_interposer(verifying))
    out = capsys.readouterr().out

    assert code == 0
    assert "detected=True" in out
    assert "repaired=True" in out
    assert "propagated=False" in out


def test_the_probe_distinguishes_noticing_from_fixing(tmp_path, monkeypatch, capsys):
    """A credulous agent shows repaired=False, and the probe prints it.

    Without that field the output cannot tell an agent that fixed the fault
    from one that merely avoided the credulous figure -- which is the
    conflation repair rate exists to remove (#16).
    """
    code = _run(monkeypatch, tmp_path, _through_the_interposer(naive))
    out = capsys.readouterr().out

    assert code == 0
    assert "repaired=False" in out
    assert "propagated=True" in out


def test_the_injected_fault_is_reported_before_the_run(tmp_path, monkeypatch, capsys):
    """So a human reading CI can see what was done to the agent."""
    _run(monkeypatch, tmp_path, _through_the_interposer(naive))
    out = capsys.readouterr().out

    scenario = hardest_scenario(TaskSpec.from_yaml(AUDITED))
    assert "injecting wrong_value on" in out
    assert any(rid in out for rid in scenario.audited_ids)
    assert "shifts the total by" in out


def test_no_faulted_skips_the_half_and_says_it(tmp_path, monkeypatch, capsys):
    """Halves the cost, and the coverage. It must not look like a pass."""
    def answers_without_looking(variant, seed):
        live_mod._final_text(object())
        return Answer(total=3635)

    code = _run(monkeypatch, tmp_path, answers_without_looking, "--no-faulted")
    out = capsys.readouterr().out

    assert code == 0
    assert "skipping the faulted half" in out
    assert "clean and faulted" not in out


def test_an_outage_on_the_faulted_half_is_still_not_a_build_failure(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fails_second_time(variant, seed):
        calls["n"] += 1
        live_mod._final_text(object())
        if calls["n"] == 1:
            return Answer(total=3635)
        raise RuntimeError("overloaded_error: the model is overloaded")

    assert _run(monkeypatch, tmp_path, fails_second_time) == 4


def test_a_missing_dependency_on_the_faulted_half_is_ours(tmp_path, monkeypatch):
    calls = {"n": 0}

    def breaks_second_time(variant, seed):
        calls["n"] += 1
        live_mod._final_text(object())
        if calls["n"] == 1:
            return Answer(total=3635)
        raise ModuleNotFoundError("No module named 'langchain_core'")

    assert _run(monkeypatch, tmp_path, breaks_second_time) == 1
