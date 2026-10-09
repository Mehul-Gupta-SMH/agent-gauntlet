"""Exhaustive coverage of one config's fault space, and what it is not (#18).

#18 asks for an *exploration mode* on the grounds that chaos engineering is
a discovery discipline. Its own argument -- you cannot find an unknown
failure mode with a checklist written in advance -- rules the thing out
here, because the adversity available is a registry of five tools and an
enum of kinds.

So these tests pin the weaker claim and the stronger property:

* the space is ENUMERABLE, so a sweep covers it completely and the
  fraction has no sampling error at all -- which no number on the
  comparison board can say;
* and it cannot find a fault CLASS nobody implemented, which is stated
  rather than left for a reader to assume.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_gauntlet import architect, explore
from agent_gauntlet.faults import ASKING_SHAPES, FaultKind
from agent_gauntlet.interpose import INJECTION_SITES
from agent_gauntlet.matrix import _allowed_tools, offline_executor
from agent_gauntlet.spec import TaskSpec

ROOT = Path(__file__).resolve().parents[1]
AUDITED = ROOT / "fixtures" / "inventory" / "audited.yaml"


@pytest.fixture
def task():
    return TaskSpec.from_yaml(AUDITED)


def _variant(task, tmp_path, prompt, toolset):
    return architect.generate(
        out_dir=tmp_path / f"v-{prompt}-{toolset}", task=task,
        models={"cheap": "anthropic/claude-haiku-4-5"},
        prompts=[prompt], toolsets=[toolset], include_sentinel=False)[0]


def _sweep(task, variant, scenario=None):
    scenario = scenario or task.scenarios[0]
    return explore.sweep(
        task=task, variant=variant, scenario=scenario,
        granted=sorted(_allowed_tools(variant)), execute=offline_executor)


def test_the_space_is_small_enough_to_enumerate(task, tmp_path):
    """The fact that makes coverage possible and discovery impossible.

    Four to ten cells per config. A space this size is not searched, it is
    covered -- and that is why the fraction below carries no interval.
    """
    scenario = task.scenarios[0]
    sizes = {}
    for toolset in ("records", "records+summary", "records+annotations",
                    "records+notes"):
        v = _variant(task, tmp_path, "naive", toolset)
        cells = explore.space(task, v, scenario,
                              granted=sorted(_allowed_tools(v)))
        sizes[toolset] = len(cells)
        assert len(set(cells)) == len(cells), "a cell appears twice"
    assert all(4 <= n <= 10 for n in sizes.values()), sizes


def test_only_granted_tools_and_audited_records_are_swept(task, tmp_path):
    """Both restrictions are established rules, not new ones.

    A fault in an ungranted tool is unreachable (#38, #40). A fault outside
    the audited subset contradicts nothing observable, so no config could
    catch it and the cell would score luck (#5, #16).
    """
    scenario = task.scenarios[0]
    v = _variant(task, tmp_path, "naive", "records")
    granted = set(_allowed_tools(v))
    cells = explore.space(task, v, scenario, granted=sorted(granted))

    assert {c.tool for c in cells} <= granted
    assert "read_annotation" not in {c.tool for c in cells}, (
        "this toolset grants no annotation tool"
    )
    keyed = {c.target for c in cells
             if c.kind in (FaultKind.WRONG_VALUE, FaultKind.OMISSION,
                           FaultKind.TIMEOUT)}
    assert keyed == set(scenario.audited_ids)
    assert keyed < set(scenario.records), "the audit is partial here"


def test_an_instruction_cell_per_asking_shape_and_no_control(task, tmp_path):
    """The control asks for nothing, so compliance is undecidable on it.

    Sweeping it would add a cell that can never decide, and the censoring
    rule already has enough work.
    """
    v = _variant(task, tmp_path, "naive", "records+annotations")
    cells = explore.space(task, v, task.scenarios[0],
                          granted=sorted(_allowed_tools(v)))
    shapes = {c.target for c in cells if c.kind is FaultKind.INSTRUCTION}
    assert shapes == {s.value for s in ASKING_SHAPES}
    assert "control" not in shapes


def test_a_cell_is_pinned_to_its_own_target_not_the_seeds_choice(task,
                                                                 tmp_path):
    """`build` picks a target from the seed, which is right for a matrix
    and wrong for a sweep: it would cover whatever it happened to pick."""
    scenario = task.scenarios[0]
    for key in scenario.audited_ids:
        cell = explore.Cell("fetch_record", FaultKind.WRONG_VALUE, key)
        schedule = explore._schedule(cell, scenario, "any-seed")
        (fault,) = schedule.faults
        assert fault.target_key == key


def test_a_cell_that_decided_nothing_is_not_one_the_config_survived(task,
                                                                    tmp_path):
    """The censoring rule, applied to the denominator rather than a cell.

    `survived` counts out of cells that decided, and the rest are reported
    separately. Counting a censored cell as survived would be the
    fail-green shape with the space itself as the number.
    """
    v = _variant(task, tmp_path, "verifying", "records+summary")
    findings = _sweep(task, v)
    ok, decided = explore.survived(findings)
    censored = explore.censored(findings)

    assert decided + len(censored) == len(findings)
    assert censored, "a timeout cell cannot decide a number task"
    assert all(f.got_through is None for f in censored)
    assert ok == decided, "this config catches everything it can be shown"
    # And the fraction is over the decided cells, never over the space.
    assert decided < len(findings)


def test_censoring_says_which_of_the_two_reasons(task, tmp_path):
    """Rule 3: not applicable and withheld are different facts.

    A timeout corrupts no number, so on a task that reports one there was
    never anything to propagate. A wrong value too small to clear the band
    COULD have decided and did not. Printing one sentence for both was a
    disjunction that told the reader nothing.
    """
    v = _variant(task, tmp_path, "verifying", "records+summary")
    findings = _sweep(task, v)
    by_kind = {}
    for f in explore.censored(findings):
        by_kind.setdefault(f.cell.kind, []).append(f)

    assert FaultKind.TIMEOUT in by_kind
    for f in by_kind[FaultKind.TIMEOUT]:
        assert not f.applicable
        assert "not applicable" in f.why_censored

    assert FaultKind.WRONG_VALUE in by_kind, (
        "one wrong-value cell on this fixture is below the band"
    )
    for f in by_kind[FaultKind.WRONG_VALUE]:
        assert f.applicable
        assert "withheld" in f.why_censored


def test_the_sweep_finds_what_the_board_gates(task, tmp_path):
    """A config without a cross-check loses every decidable cell.

    Not a new measurement -- the same facts the board reports -- which is
    the point: a sweep over one config and a ranking over many must not
    disagree about what happened.
    """
    naive = _sweep(task, _variant(task, tmp_path, "naive", "records+summary"))
    verifying = _sweep(
        task, _variant(task, tmp_path, "verifying", "records+summary"))

    assert explore.survived(naive) == (0, 3)
    assert explore.survived(verifying) == (3, 3)
    through = [f.cell.label for f in naive if f.got_through]
    assert any("omission" in lbl for lbl in through)
    assert any("wrong_value" in lbl for lbl in through)


def test_a_config_with_no_reachable_cell_is_not_a_clean_bill_of_health(
        task, tmp_path):
    """`summary` grants `get_summary` and nothing injectable.

    Zero cells means this harness cannot adversely test it at all. An empty
    space reported as "survived everything" would be the worst possible
    reading, so `survived` returns 0/0 and the report says what it means.
    """
    v = architect.generate(
        out_dir=tmp_path / "v-summary", task=task,
        models={"cheap": "anthropic/claude-haiku-4-5"},
        prompts=["naive"], toolsets=["records", "summary"],
        include_sentinel=False)
    summary_only = [x for x in v if x.factors["toolset"] == "summary"][0]

    cells = explore.space(task, summary_only, task.scenarios[0],
                          granted=sorted(_allowed_tools(summary_only)))
    assert cells == []
    assert explore.survived(_sweep(task, summary_only)) == (0, 0)


def test_coverage_cannot_find_a_class_nobody_implemented():
    """The honest ceiling, pinned as a structural fact.

    The space is the product of a registry and an enum. If a fault class is
    not in `FaultKind`, no sweep reaches it -- so this is coverage of a
    DECLARED space and never discovery. #18's framing is right and that is
    exactly why its exploration mode is not what got built.
    """
    reachable = {k for kinds in INJECTION_SITES.values() for k in kinds}
    assert reachable <= set(FaultKind), "the space is bounded by the enum"
    # Every kind in the enum has somewhere to live, or it is dead code
    # pretending to be coverage.
    assert set(FaultKind) == reachable, (
        f"{sorted(k.value for k in set(FaultKind) - reachable)} is in the "
        "enum with no injection site, so no sweep can ever reach it"
    )
