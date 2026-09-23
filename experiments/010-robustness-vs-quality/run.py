"""Experiment 010 — does robustness actually rank differently from quality?

Issue #4 is the bet the chaos layer rests on: that the happy-path champion
is often credulous under fault injection. If the clean leaderboard and the
robustness leaderboard agree, the chaos layer is expensive confirmation of
a ranking we already had.

#4 says the test is cheap, because the counterfactual pairs already exist.
It is cheaper than that -- both live matrices this project has run are
already committed, so the live half of this answer costs nothing and
spends nothing.

Three parts, and they do not agree, which is the finding:

  1. LIVE (648 real runs, two fixtures, already paid for). Parsed straight
     out of the committed console captures rather than transcribed, so this
     script cannot quietly disagree with the artifact it cites.
  2. OFFLINE (same two fixtures, scripted policies at declared slip rates).
  3. The gap between them, which is where the honest answer lives.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Optional

from agent_gauntlet import Ledger, TaskSpec, VariantSpec, run_matrix
from agent_gauntlet.analyze import kendall_tau

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent

LIVE = [
    ("003", ROOT / "experiments/003-live-m0-gate/console.txt",
     "fixtures/inventory/task.yaml"),
    ("007", ROOT / "experiments/007-m0-gate-passed/console.txt",
     "fixtures/inventory/audited.yaml"),
]

FIXTURES = [
    ROOT / "fixtures/inventory/task.yaml",
    ROOT / "fixtures/inventory/audited.yaml",
]

# Enough that the clean half's spread is not itself noise. `detectable_
# difference(30)` is about 36%, and the clean spread offline runs to 23
# points, so this is the smallest k that could see it at all.
REPEATS = 30

_ANNOTATION = re.compile(r"\s*(\[[^\]]*\]|<-\s*\w+)\s*$")


def parse_board(console: Path) -> list[tuple[str, dict[str, Optional[float]], bool]]:
    """Read the board table out of a committed console capture.

    Parsed rather than transcribed, deliberately. `experiments/README.md`
    says the artifact is the result and prose beside it can be wrong; a
    number retyped into this file would be prose.
    """
    lines = console.read_text().splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("--- board"))
    header = lines[start + 1].split()
    # The board grew a leading rank column in #44, so the variant column is
    # located rather than assumed to be first. Everything to its right is a
    # value; everything between it and the values is the name.
    assert "variant" in header, header
    columns = header[header.index("variant") + 1:]

    rows = []
    for line in lines[start + 2:]:
        if not line.strip() or line.startswith("---") or line.startswith("  "):
            break
        sentinel = "[sentinel]" in line or "sentinel" in line.split()[0]
        stripped = line
        while _ANNOTATION.search(stripped):
            stripped = _ANNOTATION.sub("", stripped)
        tokens = stripped.split()
        values = tokens[-len(columns):]
        name = " ".join(tokens[:-len(columns)])
        if header.index("variant") > 0 and name:
            # Drop the rank label, which is not part of the name.
            name = name.split(None, 1)[1] if " " in name else name
        rows.append((name, {c: _num(v) for c, v in zip(columns, values)}, sentinel))
    return rows


def _num(token: str) -> Optional[float]:
    if token == "n/a":
        return None
    return float(token.rstrip("%")) / 100 if token.endswith("%") else float(token)


def offline_halves(fixture: Path) -> tuple[list[str], list[float], list[float]]:
    """Clean-only and faulted-only correctness per contender, offline."""
    task = TaskSpec.from_yaml(fixture)
    variants = [
        VariantSpec(id=f"{m}-{p}-{t}", factors={"model": m, "prompt": p, "toolset": t})
        for m in ("cheap", "smart") for p in ("naive", "verifying")
        for t in ("records", "records+summary")
    ]
    with tempfile.TemporaryDirectory() as d:
        records = run_matrix(task=task, variants=variants,
                             ledger=Ledger(Path(d) / "runs.jsonl"),
                             base_seed="seed-a", repeats=REPEATS)
    agg: dict[tuple[str, str], list[float]] = {}
    for r in records:
        cell = agg.setdefault((r.variant_id, r.condition), [0.0, 0.0])
        cell[0] += float(r.score.correct)
        cell[1] += 1
    ids = sorted({v for v, _ in agg})
    clean = [agg[(v, "clean")][0] / agg[(v, "clean")][1] for v in ids]
    faulted = [agg[(v, "faulted")][0] / agg[(v, "faulted")][1] for v in ids]
    return ids, clean, faulted


def rule(title: str) -> None:
    print(f"\n--- {title} " + "-" * max(0, 68 - len(title)))


def main() -> int:
    print("Experiment 010 — does robustness rank differently from quality?")
    print("=" * 70)

    rule("1. LIVE: the two matrices already paid for")
    for label, console, fixture in LIVE:
        rows = [r for r in parse_board(console) if not r[2]]
        clean = [v["clean"] for _, v, _ in rows]
        fault = [v["fault"] for _, v, _ in rows]
        print(f"\n  experiment {label}  ({fixture})")
        print(f"  {'contender':<54} {'clean':>7} {'faulted':>8}")
        for (name, v, _) in rows:
            print(f"  {name[:54]:<54} {_pct(v['clean']):>7} {_pct(v['fault']):>8}")
        tau = kendall_tau(clean, fault)
        distinct = len({c for c in clean if c is not None})
        print(f"\n  contenders                 {len(rows)} (sentinel excluded)")
        print(f"  distinct clean values      {distinct}")
        print(f"  tau(clean, robustness)     {tau if tau is not None else 'UNDEFINED'}")
        if tau is None:
            print("  -> the clean ranking does not exist. Every contender answered")
            print("     correctly every time its tools told the truth, so there is")
            print("     no happy-path order for the robustness order to agree with.")
            # The obvious objection to an undefined tau is that it came from
            # binarising a continuous thing -- and `Score.accuracy` exists
            # for exactly that, to break ties a binary metric cannot. It
            # cannot break these. The task's tolerance is 0, so a run that
            # scores `correct` was exact, and an exact run grades 1.000.
            # 100% clean correctness therefore forces clean accuracy to
            # 1.000 for every contender: the graded ranking is the same tie.
            assert all(c == 1.0 for c in clean if c is not None)
            print("     Not an artefact of binarising, either: tolerance is 0, so")
            print("     100% clean correctness forces graded clean accuracy to")
            print("     1.000 for every contender. The tie-breaker ties too.")

    rule("2. OFFLINE: the same two fixtures, under declared noise")
    for fixture in FIXTURES:
        ids, clean, fault = offline_halves(fixture)
        task = TaskSpec.from_yaml(fixture)
        order = sorted(zip(ids, clean, fault), key=lambda x: -x[1])
        print(f"\n  {fixture.relative_to(ROOT)}  ({task.fingerprint()})  k={REPEATS}")
        print(f"  {'contender':<34} {'clean':>7} {'faulted':>8}")
        for v, c, f in order:
            survives = "  <- survives the fault" if f > 0 else ""
            print(f"  {v:<34} {c:>7.3f} {f:>8.3f}{survives}")
        print(f"\n  tau(clean, robustness)     {kendall_tau(clean, fault):+.3f}")

    rule("3. what the two halves mean together")
    print("""
  LIVE is the evidence and it cannot compute the statistic #4 asks for.
  On both fixtures every contender scored 100% clean, so the clean
  leaderboard is one big tie and tau is undefined -- not low, absent. The
  bet in #4 was that the happy-path champion is often credulous. On these
  tasks the stronger thing is true: there IS no happy-path champion. The
  faulted half is the only axis that ranked anything, and it ranked the
  same config first across three seeds (experiment 007).

  OFFLINE can compute it, and gets a NEGATIVE correlation on both
  fixtures. The two configurations that survive a fault sit 4th and 7th of
  8 on the clean board. The mechanism is legible: cross-checking costs a
  little clean accuracy, because every extra step is another chance to
  slip, and buys everything under fault.

  That second number is NOT evidence about real agents and must not be
  quoted as if it were. The offline policies were written by hand, with
  exactly this structure in them; finding it again is a statement about
  the harness, not about the world. What it does establish is that the
  two metrics are not redundant BY CONSTRUCTION -- the machinery can
  represent and detect a robustness ranking that contradicts the quality
  ranking, which is the precondition for the live question being
  answerable at all.

  The gap is the finding. This project has no fixture on which a live
  clean run discriminates between competent configurations, so #4's
  correlation cannot be measured live -- only bounded, and the bound is
  the strongest form of the thesis. Building such a fixture is what
  would turn an existence proof into a measurement.
""")
    return 0


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x:.0%}"


if __name__ == "__main__":
    raise SystemExit(main())
