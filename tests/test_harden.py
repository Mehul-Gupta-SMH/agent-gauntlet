"""One round of hardening an imported agent (#50, H2).

The executor here is a fake that READS the candidate it was handed -- its
skill.md and its tool grant -- and behaves accordingly. That is what makes
the plumbing test meaningful: a candidate wins only because the change was
written into its files and reached the thing running it. The fail-green
test is the same round with an executor that ignores the files, and it must
not come back hardened.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_gauntlet import architect, cli, harden, interpose
from agent_gauntlet.faults import FaultKind
from agent_gauntlet.ledger import Ledger
from agent_gauntlet.score import Answer
from agent_gauntlet.spec import TaskSpec

ROOT = Path(__file__).resolve().parents[1]
AUDITED = ROOT / "fixtures" / "inventory" / "audited.yaml"
INSTRUCTION = ROOT / "fixtures" / "poisoning" / "instruction.yaml"
SEEDS = ["s0", "s1", "s2"]


@pytest.fixture
def task():
    return TaskSpec.from_yaml(AUDITED)


def _own_agent(tmp_path, tools=("fetch_record", "list_records")):
    agent = tmp_path / "mine"
    agent.mkdir(parents=True, exist_ok=True)
    (agent / "skill.md").write_text("You add up stock. Be quick.\n",
                                    encoding="utf-8")
    (agent / "agent-config.yaml").write_text(yaml.safe_dump(
        {"name": "mine", "model": "anthropic/claude-haiku-4-5",
         "tools": list(tools)}), encoding="utf-8")
    return agent


def _baseline(task, tmp_path, **kw):
    return architect.import_agent(_own_agent(tmp_path, **kw), task=task,
                                  out_dir=tmp_path / "variants")


def _reads_its_files(variant, seed):
    """Reconciles only when it was told to AND holds the second source."""
    skill = (Path(variant.common_dir) / variant.entry_agent
             / "skill.md").read_text("utf-8")
    ledger = {r: interpose.fetch_quantity(r) for r in interpose.list_record_ids()}
    total = sum(ledger.values())
    if "reconcile" in skill and "get_summary" in architect.granted(variant):
        audited = interpose.summary_total()
        mine = sum(ledger[r] for r in interpose.audited_record_ids())
        if audited != mine:
            return Answer(total=total + audited - mine, flagged_anomaly=True)
    return Answer(total=total)


def _ignores_its_files(variant, seed):
    return Answer(total=sum(interpose.fetch_quantity(r)
                            for r in interpose.list_record_ids()))


def _round(task, tmp_path, execute, seeds=SEEDS, repeats=8):
    base = _baseline(task, tmp_path)
    return harden.run_round(
        task=task, baseline=base, scenario=task.scenarios[0], execute=execute,
        seeds=seeds, repeats=repeats, out_dir=tmp_path / "out",
        ledger=Ledger(tmp_path / "runs.jsonl"))


def test_a_change_that_reaches_the_agent_is_measured_held_out_and_exported(
        task, tmp_path):
    """The whole round, end to end: the naive baseline propagates the lie,
    the sweep says so, cross_check is proposed, built into the candidate's
    own files, measured beside the baseline, chosen on the selection seeds
    and shown better on the held-out one -- and only then exported, with a
    diff naming the change and a lineage back to the parent."""
    rnd = _round(task, tmp_path, _reads_its_files)
    assert (FaultKind.WRONG_VALUE, harden.PROPAGATED) in rnd.found
    names = [c.hardening.name for c in rnd.candidates]
    assert names[0] == "cross_check"
    built = next(c.variant for c in rnd.candidates
                 if c.hardening.name == "cross_check")
    assert built.parent == rnd.baseline.fingerprint
    assert built.factors["hardening"] == "cross_check"

    c = rnd.contrast
    assert c.verdict == harden.HARDENED, c.reason
    assert c.held.variant_id == built.id
    assert c.metric == "propagation_rate"
    # Reported on data that did not choose it.
    assert set(c.held.holdout_seeds).isdisjoint(c.held.selection_seeds)

    assert rnd.exported is not None and rnd.exported.is_dir()
    diff = (tmp_path / "out" / "hardened.diff").read_text()
    assert "+  - get_summary" in diff or "+- get_summary" in diff
    assert "reconcile" in diff


def test_a_candidate_that_changes_nothing_in_behaviour_is_not_hardened(
        task, tmp_path):
    """Fail-green: the candidates differ on disk and the thing running them
    never looks. If the round still said HARDENED it would be crediting the
    label, and nothing would be exported."""
    rnd = _round(task, tmp_path, _ignores_its_files)
    assert rnd.contrast.verdict != harden.HARDENED
    assert rnd.exported is None
    assert not (tmp_path / "out" / "hardened.diff").exists()


def _shrinks_held_out(variant, seed):
    """The winner's curse in miniature: the change works on every run of
    the seeds that choose, and on one repeat in six of the seed that
    reports. Chosen fairly, then measured smaller -- the realistic shape."""
    # seed_for: base:variant:scenario:repeat, then :clean or :faulted.
    parts = str(seed).split(":")
    if parts[0] == SEEDS[-1] and parts[-2] != "0":
        return _ignores_its_files(variant, seed)
    return _reads_its_files(variant, seed)


def test_a_partial_fix_on_a_sized_round_is_not_shown_rather_than_hardened(
        task, tmp_path):
    """A round big enough for the assumed effect, and a fix that shrinks
    on the held-out seed: chosen on the selection seeds, then 100% -> ~83%
    where it is reported, with the intervals touching. That is NOT
    hardened -- the round reports the resolution it lacked instead."""
    rnd = _round(task, tmp_path, _shrinks_held_out, repeats=6)
    assert rnd.contrast.verdict == harden.NOT_SHOWN, rnd.contrast.reason
    assert "inside noise" in rnd.contrast.reason
    assert rnd.exported is None


def test_a_round_too_small_to_show_anything_is_refused_before_it_runs(
        task, tmp_path):
    """Fail-green, one level up: an undersized round can only return
    not_shown, and returns it at full price. Refused before the sweep, so
    not one run is spent on a verdict that was certain in advance."""
    calls = []

    def counting(variant, seed):
        calls.append(seed)
        return _reads_its_files(variant, seed)

    with pytest.raises(ValueError, match="round too small"):
        _round(task, tmp_path, counting, repeats=1)
    assert calls == []


def test_the_guard_and_the_verdict_use_the_same_separation_test():
    """The guard is a best case: exact rates on n runs. Below `needed` the
    Wilson intervals of 75% and 25% touch, at `needed` they do not -- the
    same `excludes` that `contrast` gates on, so the two cannot disagree."""
    need = harden.runs_to_separate(0.75, 0.25)
    assert need == 16
    assert harden.separates(0.75, 0.25, need)
    assert not harden.separates(0.75, 0.25, need - 1)
    # An effect no run count shows is unsizeable, not "one run".
    assert harden.runs_to_separate(0.5, 0.5) is None


def test_held_out_runs_count_only_the_seeds_that_report(task):
    """Selection seeds choose and report nothing, so they do not size the
    round: 4 seeds x 3 scenarios x 6 repeats is 18 held-out runs, not 72."""
    s = harden.size(task, seeds=4, repeats=6)
    assert s.held_runs == 6 * len(task.scenarios)
    assert s.enough
    assert not harden.size(task, seeds=4, repeats=2).enough
    assert harden.repeats_needed(task) == 6


def test_live_harden_refuses_an_undersized_round_before_preflight(
        tmp_path, capsys):
    """Refused with exit 2 before credentials are even checked: sizing is
    arithmetic, and it should not need a key to say a round is hopeless."""
    rc = cli.main(["harden", str(AUDITED), "--baseline",
                   str(_own_agent(tmp_path)), "--live", "--repeats", "2",
                   "--out", str(tmp_path / "o")])
    out = capsys.readouterr().out
    assert rc == 2, out
    assert "cannot return HARDENED" in out
    assert "--repeats 6" in out
    assert "PREFLIGHT" not in out


def test_one_seed_cannot_hold_anything_out(task, tmp_path):
    """Selecting and reporting on the same runs is the winner's curse
    (#20) shipped as a result; refuse before spending a run."""
    with pytest.raises(ValueError, match="need more than 1 seed"):
        _round(task, tmp_path, _reads_its_files, seeds=["only"])


def test_a_revoke_of_the_faulted_tool_is_reported_not_dropped(tmp_path):
    """Revoking the tool the fault lands in makes the fault unreachable:
    a change of task, not of agent. It is still a real hardening, so it is
    named as uncertifiable here rather than silently left out."""
    task = TaskSpec.from_yaml(AUDITED).model_copy(update={
        "fault_tool": "read_annotation", "fault_kind": "instruction"})
    base = architect.import_agent(
        _own_agent(tmp_path, tools=("fetch_record", "list_records",
                                    "read_annotation")),
        task=task, out_dir=tmp_path / "variants")
    cand = harden.derive(base, next(h for h in harden.CATALOGUE
                                    if h.name == "revoke_annotations"),
                         task=task, out_dir=tmp_path / "out")
    assert cand.variant is None
    assert "the tool this task's fault lands in" in cand.refused


def test_structural_hardenings_are_proposed_before_prompt_ones():
    """Rule 5: a prompt is not shown to harden anything, so a tool change
    that answers the same failure ranks first."""
    out = harden.propose({(FaultKind.INSTRUCTION, harden.COMPLIED)})
    assert [h.name for h in out] == ["revoke_annotations", "anchored"]
    assert out[0].structural and not out[1].structural


def test_a_censored_cell_proposes_nothing():
    """A cell that decided nothing found no failure, so there is nothing to
    answer -- patching it would be patching noise."""
    from agent_gauntlet.explore import Cell, Finding

    f = Finding(cell=Cell(tool="fetch_record", kind=FaultKind.WRONG_VALUE,
                          target="r1"),
                answer=None, expected=1, outcome="errored", detected=False,
                propagated=False, complied=False, decidable=False,
                applicable=True)
    assert harden.failures([f]) == set()


def test_every_grant_is_a_tool_the_harness_serves():
    """A grant the harness cannot serve would be refused by import_agent at
    derive time -- for every operator, on every task. Catch it here."""
    for h in harden.CATALOGUE:
        assert set(h.grant) <= set(architect.HARNESS_TOOLS), h.name


def test_lineage_fields_leave_existing_fingerprints_alone(task, tmp_path):
    """`parent` and `hardening` are bookkeeping on the spec, not part of
    the agent: the same files must hash the same however they were made."""
    base = _baseline(task, tmp_path)
    tagged = base.model_copy(update={"parent": "abc", "hardening": "x"})
    assert tagged.fingerprint == base.fingerprint


def test_offline_harden_is_a_plan_with_improvement_not_measured(
        tmp_path, capsys):
    """Offline cannot run an imported agent at all, so the command plans:
    what could fail, what would be proposed, candidates on disk -- and the
    improvement printed n/a, never a number."""
    rc = cli.main(["harden", str(AUDITED), "--baseline",
                   str(_own_agent(tmp_path)), "--out", str(tmp_path / "o")])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "cross_check" in out
    assert "improvement n/a" in out
    assert "round       1 of 1" in out
    assert "needs at least 16" in out
    assert list((tmp_path / "o" / "variants").iterdir())


def test_offline_harden_on_an_instruction_task_proposes_the_revoke_as_unmeasurable(
        tmp_path, capsys):
    """The instruction fixture faults read_annotation, so the strongest fix
    removes the instrument. Printed as such, beside the measurable one."""
    agent = _own_agent(tmp_path, tools=("fetch_record", "list_records",
                                        "read_annotation"))
    rc = cli.main(["harden", str(INSTRUCTION), "--baseline", str(agent),
                   "--out", str(tmp_path / "o")])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "revoke_annotations" in out and "not certifiable here" in out
    assert "anchored" in out
    # Rule 2d: a wrong-value fix cannot be judged under an instruction fault.
    assert "cross_check" not in out
    assert "not this task's fault kind" in out
