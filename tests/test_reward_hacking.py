"""The specification-gaming failure that is reachable today (#6).

#6's attack is a variant writing a tool that returns what the check wants.
That has nowhere to happen here: variants are assembled from a fixed
catalogue of prompts and tool sets, and the issue's own third suggestion --
"shared verified tool pool as the default" -- is what already ships.

What an operator CAN do, by accident, in one line, is write an answer into
the statement they are about to grade against. Every variant then scores
1.00 without calling anything and the board prints a winner. Same
fail-green shape as a fault that cannot fire, refused in the same place.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_gauntlet import TaskSpec, architect
from agent_gauntlet.architect import MIN_LEAKABLE, leaks_answer

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "inventory" / "audited.yaml"
MODELS = {"cheap": "m", "smart": "s"}


@pytest.fixture
def task() -> TaskSpec:
    return TaskSpec.from_yaml(FIXTURE)


def test_every_shipped_fixture_is_clean():
    """A regression guard over the real thing, not a crafted example. The
    check is worth nothing if it cannot be run against the fixtures."""
    for fixture in sorted(ROOT.glob("fixtures/*/*.yaml")):
        spec = TaskSpec.from_yaml(fixture)
        assert leaks_answer(spec) is None, f"{fixture.name}: {leaks_answer(spec)}"


def test_the_answer_in_the_statement_is_refused(task):
    total = task.scenarios[0].expected_total
    leaky = task.model_copy(update={
        "statement": f"{task.statement}\n\nFor reference the total is {total}."})
    why = leaks_answer(leaky)
    assert why and str(total) in why


def test_the_audited_figure_counts_too(task):
    """It is not the answer, but it is most of it -- and on a partial audit
    a config that reads it off the prompt skips the reconciliation the
    fixture exists to measure."""
    audited = task.scenarios[0].audited_total
    assert audited != task.scenarios[0].expected_total
    leaky = task.model_copy(update={
        "statement": f"{task.statement}\n\nThe audit covers {audited} units."})
    assert leaks_answer(leaky) is not None


def test_generation_refuses_rather_than_warning(task, tmp_path):
    """A warning on a full leaderboard is read as a footnote. The matrix
    must not exist."""
    total = task.scenarios[0].expected_total
    leaky = task.model_copy(update={"statement": f"total is {total}"})
    with pytest.raises(ValueError, match="leaks its own answer"):
        architect.generate(out_dir=tmp_path / "v", task=leaky, models=MODELS)


def test_a_substring_is_not_a_leak(task):
    """`128` inside `31280` is not the answer written down, and a check
    that fires on it would be turned off within a week."""
    total = task.scenarios[0].expected_total
    assert total >= MIN_LEAKABLE
    near = task.model_copy(update={
        "statement": f"{task.statement}\n\nOrder reference {total}9{total}."})
    assert leaks_answer(near) is None


def test_small_numbers_do_not_trip_it(task):
    """"say so within 3 steps" is ordinary prose. A single digit colliding
    with a total is a coincidence, and a refusal that fires on prose is a
    refusal somebody deletes."""
    tiny = task.model_copy(update={
        "scenarios": [task.scenarios[0].model_copy(update={
            "records": {"a": 3}, "audited": [], "expected": 3})],
        "statement": "Total the records. Respond within 3 steps.",
    })
    assert leaks_answer(tiny) is None


def test_the_static_prompt_surface_carries_no_fixture_answer():
    """The other half of the surface, and the half operators do not
    control. A prompt strategy or a tool docstring holding a fixture's
    total would leak it into every task that used it."""
    surface = "\n".join(architect.PROMPTS.values()) + architect._TOOLS_PY
    for fixture in sorted(ROOT.glob("fixtures/*/*.yaml")):
        spec = TaskSpec.from_yaml(fixture)
        for scenario in spec.scenarios:
            for value in (scenario.expected_total, scenario.audited_total):
                if abs(value) < MIN_LEAKABLE:
                    continue
                assert str(value) not in surface, (
                    f"{fixture.name}/{scenario.id}: {value} appears in the "
                    "shared prompt surface"
                )
