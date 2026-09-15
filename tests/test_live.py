"""The live path's testable halves: preflight and answer parsing.

Actual execution against a model is not exercised here -- it needs
credentials and costs money. What *is* exercised is everything that decides
whether a run should start and how its reply is read, which is where the
mistakes that waste money or silently corrupt a score live.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_gauntlet import TaskSpec, architect
from agent_gauntlet.live import ANSWER_FORMAT, MissingCredentials, parse_answer, preflight

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "inventory" / "task.yaml"
MODELS = {"cheap": "openai/gpt-4o-mini", "smart": "anthropic/claude-sonnet-5"}


@pytest.fixture
def task() -> TaskSpec:
    return TaskSpec.from_yaml(FIXTURE)


# --- answer parsing -------------------------------------------------------


def test_parses_the_structured_contract():
    answer = parse_answer("Here is my working.\n\nTOTAL: 107\nANOMALY: no\n")
    assert answer.total == 107 and not answer.flagged_anomaly


def test_parses_a_flagged_anomaly():
    answer = parse_answer("The records disagree.\n\nTOTAL: 107\nANOMALY: yes")
    assert answer.total == 107 and answer.flagged_anomaly


def test_tolerates_thousands_separators():
    assert parse_answer("TOTAL: 3,635\nANOMALY: no").total == 3635


def test_unformatted_reply_is_unanswered_not_guessed():
    """The important one.

    Regexing the last number out of prose would turn "ignored the output
    format" into "answered correctly" often enough to matter, and the
    failure would be invisible in the board. An off-contract reply must
    score as unanswered.
    """
    answer = parse_answer("I counted 3 records and the total came to 107.")
    assert answer.total is None
    assert not answer.flagged_anomaly


def test_prose_containing_numbers_is_not_mined():
    answer = parse_answer("Totals: 12, 34, 56. The sum is obviously 102.")
    assert answer.total is None


def test_empty_reply_is_unanswered():
    assert parse_answer("").total is None
    assert parse_answer(None).total is None  # type: ignore[arg-type]


def test_malformed_total_is_unanswered():
    assert parse_answer("TOTAL: many\nANOMALY: no").total is None


# --- the contract reaches the variants ------------------------------------


def test_generated_skill_carries_the_output_contract(task, tmp_path):
    """A parser contract nothing tells the agent about is not a contract."""
    variants = architect.generate(out_dir=tmp_path / "v", task=task, models=MODELS)
    for v in variants:
        skill = (Path(v.common_dir) / "auditor" / "skill.md").read_text()
        assert "TOTAL:" in skill and "ANOMALY:" in skill
    assert "TOTAL:" in ANSWER_FORMAT


# --- preflight ------------------------------------------------------------


def test_preflight_refuses_before_spending(task, tmp_path, monkeypatch):
    """Fail on the missing credential, not on the first run that needs it."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    variants = architect.generate(out_dir=tmp_path / "v", task=task, models=MODELS)
    with pytest.raises(MissingCredentials) as exc:
        preflight(variants, target="claude")
    message = str(exc.value)
    assert "ANTHROPIC_API_KEY" in message, "the error must name the variable"
    assert "9 of 9 variants" in message


def test_preflight_passes_when_credentials_are_present(task, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    variants = architect.generate(out_dir=tmp_path / "v", task=task, models=MODELS)
    preflight(variants, target="claude")  # must not raise


def test_generated_variants_declare_their_credential(task, tmp_path):
    """Names only, never values -- commonadk's contract."""
    import yaml

    variants = architect.generate(out_dir=tmp_path / "v", task=task, models=MODELS)
    smart = next(v for v in variants if v.factors.get("model") == "smart")
    cfg = yaml.safe_load(
        (Path(smart.common_dir) / "auditor" / "agent-config.yaml").read_text()
    )
    env = cfg["requires"]["env"]
    assert [e["name"] for e in env] == ["ANTHROPIC_API_KEY"]
    assert all("value" not in e for e in env), "a spec must never carry a secret"
