"""The documentation's checkable claims, checked.

Architecture docs rot silently. The LLD's module map carried line counts and
a dependency graph that were accurate the day they were written and wrong
within a week -- six modules missing, every count stale -- and nothing
failed, because nothing was looking. That is the same fail-green shape the
rest of this repo is about: a claim that is never verified reads as true.

So the claims a machine can check are checked here. The ones it cannot --
whether the prose is *right* -- still need a reader, and no test pretends
otherwise.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "agent_gauntlet"
LLD = ROOT / "docs" / "architecture" / "lld.md"
HLD = ROOT / "docs" / "architecture" / "hld.md"
README = ROOT / "README.md"


def modules() -> dict[str, int]:
    return {
        f.stem: len(f.read_text(encoding="utf-8").splitlines())
        for f in sorted(SRC.glob("*.py"))
        if f.stem != "__init__"
    }


def test_the_module_map_lists_every_module():
    """A map missing a module is worse than no map: it implies the thing it
    omits does not exist."""
    text = LLD.read_text(encoding="utf-8")
    missing = [m for m in modules() if f"<b>{m}</b>" not in text]
    assert not missing, f"lld.md module map is missing: {missing}"


def test_the_module_map_line_counts_are_current():
    """They are a rough sense of weight, and a wrong one misleads about
    where the complexity actually sits.

    A tolerance rather than an exact match: the point is the order of
    magnitude, and a test that fails on every one-line edit gets deleted.
    """
    text = LLD.read_text(encoding="utf-8")
    drift = []
    for name, real in modules().items():
        found = re.search(rf"<b>{name}</b> · (\d+)", text)
        if not found:
            continue
        claimed = int(found.group(1))
        if abs(claimed - real) > max(25, real * 0.15):
            drift.append(f"{name}: doc says {claimed}, file has {real}")
    assert not drift, "lld.md line counts have drifted: " + "; ".join(drift)


def test_every_test_file_appears_in_the_test_layout():
    text = LLD.read_text(encoding="utf-8")
    missing = [
        f.stem for f in sorted((ROOT / "tests").glob("test_*.py"))
        # `test_probe_*` is listed as a glob, and this file documents itself
        # nowhere on purpose -- it is scaffolding, not architecture.
        if f.stem not in text
        and not (f.stem.startswith("test_probe_") and "test_probe_*" in text)
        and f.stem != "test_docs"
    ]
    assert not missing, f"lld.md test layout is missing: {missing}"


def test_every_fault_kind_is_documented():
    """The taxonomy grew from two to four and the HLD did not notice for an
    hour. A kind nobody documented is a kind nobody will use."""
    from agent_gauntlet.faults import FaultKind

    text = HLD.read_text(encoding="utf-8")
    missing = [k.value for k in FaultKind if f"`{k.value}`" not in text]
    assert not missing, f"hld.md does not document fault kinds: {missing}"


def test_every_toolset_is_documented():
    from agent_gauntlet.architect import TOOLSETS

    text = LLD.read_text(encoding="utf-8")
    missing = [name for name in TOOLSETS if f"`{name}`" not in text]
    assert not missing, f"lld.md tool-grant table is missing: {missing}"


def test_the_fixture_fingerprints_in_claude_md_are_real():
    """`CLAUDE.md` lists them as the bars that must not move. A stale table
    there would tell the next session the opposite of the truth."""
    from agent_gauntlet import TaskSpec

    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    checked = 0
    for fixture in sorted(ROOT.glob("fixtures/*/*.yaml")):
        rel = str(fixture.relative_to(ROOT))
        if rel not in text:
            pytest.fail(f"CLAUDE.md does not list {rel}")
        real = TaskSpec.from_yaml(fixture).fingerprint()
        row = next(ln for ln in text.splitlines() if rel in ln)
        assert real in row, f"{rel}: CLAUDE.md says {row.strip()}, real is {real}"
        checked += 1
    assert checked == 6


def test_the_readme_documents_every_cli_command():
    """The entry point people meet first."""
    from agent_gauntlet.cli import build_parser

    text = README.read_text(encoding="utf-8")
    sub = next(
        a for a in build_parser()._actions if getattr(a, "choices", None)
        and "run" in getattr(a, "choices", {})
    )
    missing = [c for c in sub.choices if f"gauntlet {c}" not in text]
    assert not missing, f"README does not show: {missing}"
