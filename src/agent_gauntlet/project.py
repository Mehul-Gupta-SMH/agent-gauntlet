"""One project at a time: intake, tools, credentials, models, run state.

A project is the unit an operator works in. It is a directory, not a row in
a database, because everything in it has to be inspectable after the fact:
the uploaded tool files exactly as uploaded, the declarations exactly as
submitted, the ledger, and the board. An experiment write-up that cannot be
re-read from disk is a screenshot.

Deliberately one at a time. The matrix saturates the machine it runs on, and
two projects interleaving would make the per-run timings -- which feed the
cost and latency columns -- measure contention rather than the agent.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from .interpose import ToolCost
from .usertools import UserTool

SAFE_NAME = re.compile(r"[^a-z0-9._-]+")


def slug(text: str, fallback: str = "project") -> str:
    out = SAFE_NAME.sub("-", (text or "").strip().lower()).strip("-._")
    return out[:48] or fallback


class ModelChoice(BaseModel):
    """One model the operator has selected, and whether it can actually run."""

    alias: str
    """The grid level -- `cheap`, `smart`. This is the factor value; the
    board attributes to it."""
    model: str
    """The resolved provider string, e.g. `anthropic/claude-haiku-4-5`. Kept
    beside the alias because an alias alone made "which model was this?"
    unanswerable from a run record."""
    credential: Optional[str] = None
    """Environment variable NAME the provider needs. Never its value."""


class Scenario(BaseModel):
    """One world the contenders are measured in."""

    id: str = "scenario_1"
    inputs: list[list[Any]] = Field(default_factory=list)
    """Argument tuples the tools are calibrated over. These are the only
    inputs the gauntlet can serve, because they are the only ones it holds a
    truth for."""
    expected: Optional[int] = None
    """The right answer, when the operator knows it.

    Optional on purpose. Propagation -- the gate -- is decided by comparing
    a faulted run against the *same variant's clean run*, which needs no
    label. Accuracy does need one, and reads `n/a` without it rather than
    being quietly inferred.
    """


class Project(BaseModel):
    """Everything an operator declared, plus where its run output lives."""

    id: str
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    name: str = ""
    statement: str = ""
    """What the agent should do. Passed verbatim and identically to every
    contender -- one of the fairness invariants."""

    tools: list[UserTool] = Field(default_factory=list)
    builtin_tools: list[str] = Field(default_factory=list)
    """Tools from the bundled inventory the operator also wants in play."""

    models: list[ModelChoice] = Field(default_factory=list)
    prompts: list[str] = Field(default_factory=list)
    scenarios: list[Scenario] = Field(default_factory=list)

    fault_tool: str = ""
    repeats: int = 2
    seeds: int = 2
    offline: bool = True
    """Scripted policies and no spend. The default, and the only mode that
    runs without a credential."""

    stage: str = "describe"
    """describe | tools | models | ready | running | done | failed."""
    error: Optional[str] = None

    # --- persistence ------------------------------------------------------

    @property
    def dir(self) -> Path:
        return root() / self.id

    @property
    def tools_dir(self) -> Path:
        return self.dir / "tools"

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.tools_dir.mkdir(exist_ok=True)
        (self.dir / "project.json").write_text(
            self.model_dump_json(indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, project_id: str) -> Project:
        path = root() / project_id / "project.json"
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def delete(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    # --- readiness --------------------------------------------------------

    def missing_credentials(self) -> list[str]:
        """Declared credential names absent from the environment.

        Presence only. The value is never read -- not by this method, not
        anywhere else in the project -- so a missing credential is reported
        as a name and a wrong one is the provider's problem, not something
        this code can see.
        """
        names: list[str] = []
        for tool in self.tools:
            names.extend(tool.requires_credentials)
        if not self.offline:
            names.extend(m.credential for m in self.models if m.credential)
        return sorted({n for n in names if n and not os.environ.get(n)})

    def blockers(self) -> list[str]:
        """Everything standing between this project and a run.

        Reported as a list rather than raised one at a time, so the operator
        fixes the whole form once instead of discovering the next problem
        after each submission.
        """
        out: list[str] = []
        if not self.statement.strip():
            out.append("describe what the agent should do")
        if not self.tool_names():
            out.append("add at least one tool")
        if not self.prompts:
            out.append("pick at least one prompt strategy")
        if not self.models:
            out.append("pick at least one model")
        if not self.scenarios or not self.scenarios[0].inputs:
            out.append("give the tools at least one input to be calibrated over")
        if not self.fault_tool:
            out.append("choose which tool the harness should corrupt")
        elif self.fault_tool not in self.tool_names():
            out.append(
                f"{self.fault_tool} is not a tool in this project, so the fault "
                "could never fire"
            )
        for name in self.missing_credentials():
            out.append(f"environment variable {name} is not set")
        return out

    def tool_names(self) -> list[str]:
        return [t.name for t in self.tools] + list(self.builtin_tools)

    def material_tools(self) -> list[str]:
        return [t.name for t in self.tools if t.cost is ToolCost.MATERIAL]


# --- the project root -----------------------------------------------------


def root() -> Path:
    """Where projects live.

    `GAUNTLET_HOME` when set, so a CI run or a test never writes into the
    operator's real project list.
    """
    return Path(os.environ.get("GAUNTLET_HOME") or (Path.home() / ".agent-gauntlet"))


def new(name: str) -> Project:
    """Create a project with a unique directory.

    The id is derived from the name and then suffixed until it is free --
    rather than overwriting -- because a project directory holds uploaded
    source the operator may not have a copy of.
    """
    base = slug(name)
    candidate, n = base, 2
    while (root() / candidate).exists():
        candidate, n = f"{base}-{n}", n + 1
    project = Project(id=candidate, name=name.strip() or candidate)
    project.save()
    return project


def listing() -> list[dict[str, Any]]:
    """Every project on disk, newest first."""
    out = []
    if not root().exists():
        return out
    for path in root().iterdir():
        manifest = path / "project.json"
        if not manifest.is_file():
            continue
        try:
            p = Project.model_validate_json(manifest.read_text(encoding="utf-8"))
        except Exception:
            continue  # a half-written project is not a reason to fail the list
        out.append({"id": p.id, "name": p.name, "stage": p.stage,
                    "created_at": p.created_at, "tools": len(p.tools)})
    return sorted(out, key=lambda r: r["created_at"], reverse=True)
