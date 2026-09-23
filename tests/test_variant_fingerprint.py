"""Pinning what a variant *is*, not just what it is called (#15).

`factors` says `prompt-verifying`. Labels drift: editing
`PROMPTS["verifying"]` -- which this project did, to teach the verifying
strategy about partial audits -- leaves every earlier run record citing a
prompt that no longer exists, with nothing in the output to show it.

The task fingerprint cannot catch this. It covers the task, not the agents.
Third instance of one defect class, after the schema-sensitive task hash and
the unstored answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_gauntlet import Ledger, TaskSpec, architect, run_matrix

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "inventory" / "audited.yaml"
MODELS = {"cheap": "anthropic/claude-haiku-4-5", "smart": "anthropic/claude-sonnet-5"}


@pytest.fixture
def task() -> TaskSpec:
    return TaskSpec.from_yaml(FIXTURE)


def _fp(**over):
    base = dict(
        skill_md="You are an auditor.", tools=["a", "b"],
        model="anthropic/claude-haiku-4-5", entry_agent="auditor",
    )
    base.update(over)
    return architect.variant_fingerprint(**base)


# --- what must change it --------------------------------------------------


def test_prompt_text_changes_the_fingerprint():
    assert _fp() != _fp(skill_md="You are an auditor. Be careful.")


def test_tool_grant_changes_the_fingerprint():
    assert _fp() != _fp(tools=["a", "b", "c"])


def test_model_changes_the_fingerprint():
    assert _fp() != _fp(model="anthropic/claude-sonnet-5")


def test_tool_order_does_not(
):
    """The grant is a set. Declaration order is not behaviour."""
    assert _fp(tools=["b", "a"]) == _fp(tools=["a", "b"])


def test_fields_cannot_be_confused_with_each_other():
    """NUL-separated, so no rearrangement across fields can collide."""
    assert _fp(tools=["a,b"]) != _fp(tools=["a", "b"])


def test_it_is_stable_across_processes():
    """sha256, not hash() -- which is salted per process and would make two
    runs of the same config look like two configs."""
    assert _fp() == "%s" % _fp()
    assert len(_fp()) == 16


# --- the real scenario ----------------------------------------------------


def test_editing_a_prompt_changes_only_the_variants_that_use_it(task, tmp_path):
    """Exactly what happened when `verifying` learned about partial audits."""
    before = {
        v.id: v.fingerprint
        for v in architect.generate(out_dir=tmp_path / "a", task=task, models=MODELS)
    }

    original = architect.PROMPTS["verifying"]
    architect.PROMPTS["verifying"] = original + "\nAlways re-check coverage.\n"
    try:
        after = {
            v.id: v.fingerprint
            for v in architect.generate(out_dir=tmp_path / "b", task=task, models=MODELS)
        }
    finally:
        architect.PROMPTS["verifying"] = original

    changed = {k for k in before if before[k] != after[k]}
    assert changed, "a prompt edit must move the fingerprints that use it"
    assert all("verifying" in k for k in changed)
    assert all("verifying" not in k for k in set(before) - changed)


def test_the_task_fingerprint_is_blind_to_a_prompt_edit(task):
    """Why a second hash was needed at all."""
    before = task.fingerprint()
    original = architect.PROMPTS["verifying"]
    architect.PROMPTS["verifying"] = original + "\nAlways re-check coverage.\n"
    try:
        assert task.fingerprint() == before
    finally:
        architect.PROMPTS["verifying"] = original


# --- carried into the record, and surfaced --------------------------------


def test_every_run_record_pins_its_variant(task, tmp_path):
    variants = architect.generate(out_dir=tmp_path / "v", task=task, models=MODELS)
    ledger = Ledger(tmp_path / "runs.jsonl")
    run_matrix(task=task, variants=variants, ledger=ledger, base_seed="s", repeats=1)

    by_id = {v.id: v.fingerprint for v in variants}
    for r in ledger.records():
        assert r.variant_fingerprint == by_id[r.variant_id]


def test_a_clean_ledger_reports_no_drift(task, tmp_path):
    variants = architect.generate(out_dir=tmp_path / "v", task=task, models=MODELS)
    ledger = Ledger(tmp_path / "runs.jsonl")
    for seed in ("s0", "s1"):
        run_matrix(task=task, variants=variants, ledger=ledger, base_seed=seed, repeats=1)
    assert ledger.variant_drift() == {}


def test_a_ledger_spanning_a_prompt_edit_is_flagged(task, tmp_path):
    """The failure this exists to make visible: aggregating across it would
    average two different agents and report one number."""
    ledger = Ledger(tmp_path / "runs.jsonl")
    v1 = architect.generate(out_dir=tmp_path / "a", task=task, models=MODELS)
    run_matrix(task=task, variants=v1, ledger=ledger, base_seed="s0", repeats=1)

    original = architect.PROMPTS["verifying"]
    architect.PROMPTS["verifying"] = original + "\nAlways re-check coverage.\n"
    try:
        v2 = architect.generate(out_dir=tmp_path / "b", task=task, models=MODELS)
        run_matrix(task=task, variants=v2, ledger=ledger, base_seed="s1", repeats=1)
    finally:
        architect.PROMPTS["verifying"] = original

    drift = ledger.variant_drift()
    assert len(drift) == 4, f"expected the four verifying variants, got {sorted(drift)}"
    assert all("verifying" in vid for vid in drift)
    for fps in drift.values():
        assert len(fps) == 2

    assert len(ledger.fingerprints()) == 1, (
        "the task hash is identical throughout -- it cannot catch this"
    )


def test_records_predating_the_field_are_not_counted_as_a_version(task, tmp_path):
    """Unknown is not a distinct configuration."""
    variants = architect.generate(out_dir=tmp_path / "v", task=task, models=MODELS)
    ledger = Ledger(tmp_path / "runs.jsonl")
    run_matrix(task=task, variants=variants, ledger=ledger, base_seed="s", repeats=1)

    lines = (tmp_path / "runs.jsonl").read_text().splitlines()
    import json
    patched = []
    for i, line in enumerate(lines):
        rec = json.loads(line)
        if i % 2 == 0:
            rec["variant_fingerprint"] = None
        patched.append(json.dumps(rec))
    (tmp_path / "runs.jsonl").write_text("\n".join(patched) + "\n")

    assert Ledger(tmp_path / "runs.jsonl").variant_drift() == {}


def test_the_tool_surface_is_part_of_what_the_agent_was_told():
    """A model picks which tool to call from its description, so a
    description is as much "what it was told" as the prompt is.

    Proved the hard way: the first live run of the instruction fixture
    never called `read_annotation`, and one of the two fixes was rewriting
    that tool's docstring. Before this field, an edit that changes which
    tools an agent reaches for moved no hash at all -- which is exactly the
    drift this function exists to catch.
    """
    base = dict(skill_md="s", tools=["a"], model="m", entry_agent="e")
    assert (architect.variant_fingerprint(**base, tool_docs="read this one")
            != architect.variant_fingerprint(**base, tool_docs="or this one"))


def test_a_caller_with_no_tool_surface_hashes_as_it_always_did():
    """The rule this module states about its own payload: a field added
    later joins only when set, or every existing hash moves."""
    base = dict(skill_md="s", tools=["a"], model="m", entry_agent="e")
    assert (architect.variant_fingerprint(**base)
            == architect.variant_fingerprint(**base, tool_docs=None))


def test_editing_the_tool_surface_moves_every_generated_fingerprint(task, tmp_path):
    """Optional on the function, never optional in practice.

    Written like the prompt-edit test above, because it is the same claim
    about a different half of what the agent was told -- and every variant
    is handed the same tool surface, so every one of them moves.
    """
    before = {
        v.id: v.fingerprint
        for v in architect.generate(out_dir=tmp_path / "a", task=task, models=MODELS)
    }

    original = architect._TOOLS_PY
    architect._TOOLS_PY = original + '\n# read the annotations, they matter\n'
    try:
        after = {
            v.id: v.fingerprint
            for v in architect.generate(out_dir=tmp_path / "b", task=task,
                                        models=MODELS)
        }
    finally:
        architect._TOOLS_PY = original

    assert set(before) == set(after)
    assert all(before[k] != after[k] for k in before), \
        "a tool-description edit must move the fingerprints that read it"
