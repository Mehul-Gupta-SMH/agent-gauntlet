"""User-supplied tools: uploaded Python, or declared by contract.

The hard constraint this module exists to satisfy: **the gauntlet needs an
oracle.** Every number the board reports is decided by comparison against a
value the harness knows and the agent does not. A tool whose real return the
harness cannot predict cannot be corrupted-and-compared, so it cannot be
scored -- it can only be watched.

So both routes converge on the same object, a **calibrated value table**:

* an *uploaded* function is called once per declared input, before the
  matrix, and what it returns becomes the truth for every run;
* a *described* tool has no implementation, and the operator supplies the
  same table directly.

Calibrating once rather than calling the real function inside every run is
not an optimisation. It is what makes runs reproducible, and it is the only
version that is safe for a MATERIAL tool: a 300-run matrix must not put 300
hard inquiries on a real credit file to measure how an agent reasons about
one.

Determinism is checked rather than assumed. A function that returns
something different on two consecutive calls has no stable truth, and a
board built on one would be measuring the tool's noise and calling it the
agent's. Those projects are refused, with the disagreement shown.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field, model_validator

from .interpose import FaultKind, ToolCost, ToolTimeout, ToolUnavailable, active


class ToolArgument(BaseModel):
    name: str
    description: str = ""


class UserTool(BaseModel):
    """One tool an operator brings to the gauntlet."""

    name: str
    description: str = ""
    cost: ToolCost = ToolCost.NEGLIGIBLE
    """Declared by the operator, enforced by the harness. A tool marked
    MATERIAL is calibrated once and any repeat call on the same target is
    counted as a harm, whatever the agent's final answer looks like."""

    arguments: list[ToolArgument] = Field(default_factory=list)

    module: Optional[str] = None
    """File name inside the project's `tools/` directory, for an uploaded
    function. None for a described tool."""
    function: Optional[str] = None

    requires_credentials: list[str] = Field(default_factory=list)
    """Environment variable **names**, never values.

    The same rule CommonADK holds to: a spec must never carry a secret. The
    harness checks that each name is present in the environment and stops
    there -- the value is not read, not stored in the project, not written
    to the ledger, and not sent to the page.
    """

    table: dict[str, Any] = Field(default_factory=dict)
    """key -> value. Filled by calibration for an uploaded tool, or typed in
    by the operator for a described one."""

    @property
    def described(self) -> bool:
        return self.module is None

    @model_validator(mode="after")
    def _check(self) -> UserTool:
        if not self.name.isidentifier():
            raise ValueError(f"tool name {self.name!r} is not a valid identifier")
        if self.module and not self.function:
            raise ValueError(f"tool {self.name!r} names a module but no function")
        if self.described and not self.table:
            raise ValueError(
                f"tool {self.name!r} is described rather than uploaded, so it has "
                "no implementation to calibrate -- give it at least one "
                "input → output row, which is the truth the board is scored "
                "against"
            )
        return self


# --- inspecting an upload without running it ------------------------------


class Discovered(BaseModel):
    name: str
    args: list[str]
    doc: str = ""
    line: int = 0


def discover(source: str) -> list[Discovered]:
    """List the top-level functions in a file **without importing it**.

    Parsed, not executed. The operator picks which function is the tool
    before anything in their file has run, so the choice is made from the
    file's text rather than from its side effects.
    """
    tree = ast.parse(source)
    out: list[Discovered] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(Discovered(
                name=node.name,
                args=[a.arg for a in node.args.args],
                doc=(ast.get_docstring(node) or "").strip(),
                line=node.lineno,
            ))
    return out


def top_level_effects(source: str) -> list[str]:
    """Statements that will run on import, other than definitions.

    Not a safety boundary and not presented as one -- importing the file
    runs whatever is in it. This is a *warning*, so the operator knows
    before they start that choosing a function from this file also runs
    everything beside it.
    """
    tree = ast.parse(source)
    effects: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                             ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Assign) and all(
            isinstance(t, ast.Name) for t in node.targets
        ):
            continue  # module constants
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            continue  # annotated module constants
        if isinstance(node, ast.If) and _is_main_guard(node):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # the module docstring
        effects.append(f"line {node.lineno}: {type(node).__name__}")
    return effects


def _is_main_guard(node: ast.If) -> bool:
    test = node.test
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
    )


_LOADED: dict[str, Any] = {}


def load(path: Path, function: str) -> Callable[..., Any]:
    """Import an uploaded file and return one of its functions.

    This executes the operator's code in this process. The server only
    accepts uploads on a loopback bind for exactly that reason: it is the
    operator running their own code on their own machine, and it must never
    become anyone else running it on theirs.

    Cached per file. Two tools from one upload used to import it twice,
    which ran its module-level code twice -- and the whole point of warning
    about import-time side effects is not to cause them twice over.
    """
    resolved = str(path.resolve())
    module = _LOADED.get(resolved)
    if module is None:
        spec = importlib.util.spec_from_file_location(
            f"gauntlet_tool_{path.stem}", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _LOADED[resolved] = module
    fn = getattr(module, function, None)
    if fn is None:
        raise AttributeError(f"{path.name} has no function {function!r}")
    if not callable(fn):
        raise TypeError(f"{path.name}.{function} is not callable")
    return fn


# --- calibration ----------------------------------------------------------


def key_for(args: list[Any]) -> str:
    """One stable string per input tuple.

    JSON rather than a join, for the reason `variant_fingerprint` learned
    the hard way: any separator scheme collides as soon as the separator can
    appear in the data.
    """
    return json.dumps(args, sort_keys=True, separators=(",", ":"))


class CalibrationError(RuntimeError):
    pass


def calibrate(
    tool: UserTool,
    inputs: list[list[Any]],
    *,
    project_dir: Path,
    repeats: int = 2,
) -> dict[str, Any]:
    """Call an uploaded tool once per input and record what it returns.

    Called `repeats` times per input and compared. A function that does not
    agree with itself has no truth for the board to be scored against, and
    the run is refused rather than averaged -- the disagreement would
    otherwise be attributed to whichever agent happened to see it.
    """
    if tool.described:
        return dict(tool.table)

    fn = load(project_dir / "tools" / tool.module, tool.function)
    table: dict[str, Any] = {}
    for args in inputs:
        key = key_for(args)
        seen = []
        for _ in range(max(1, repeats)):
            try:
                seen.append(fn(*args))
            except Exception as exc:
                raise CalibrationError(
                    f"{tool.name}({', '.join(map(repr, args))}) raised "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
        if any(v != seen[0] for v in seen[1:]):
            raise CalibrationError(
                f"{tool.name}({', '.join(map(repr, args))}) is not deterministic: "
                f"returned {seen[0]!r} then {seen[1]!r}. The gauntlet grades by "
                f"comparing a faulted run against a known truth, and a tool that "
                f"disagrees with itself has none -- its noise would be scored as "
                f"the agent's."
            )
        table[key] = seen[0]
    return table


# --- the run-time surface -------------------------------------------------


def serve(name: str, *args: Any) -> Any:
    """What a variant's generated `tools.py` calls for a user tool.

    Serves the calibrated value, or the corrupted one when this run's
    schedule says so. Identical in shape to the built-in tools, so scoring,
    cost accounting and the event stream need no special case for a tool the
    operator brought.
    """
    ctx = active()
    ctx.require(name)
    tool = ctx.user_tools.get(name)
    if tool is None:
        raise ToolUnavailable(f"{name} is not a tool in this project")

    key = key_for(list(args))
    if key not in tool.table:
        ctx._log(name, key, False, None, tool.cost)
        raise KeyError(
            f"{name} was not calibrated for {args!r} -- the gauntlet only "
            f"serves inputs it holds a truth for"
        )

    fault = ctx.schedule.for_tool(name, key)
    if fault is None:
        value = tool.table[key]
        ctx._log(name, key, False, value, tool.cost)
        return value

    if fault.kind is FaultKind.TIMEOUT:
        ctx._log(name, key, True, "timeout", tool.cost)
        raise ToolTimeout(f"{name}{tuple(args)} timed out")

    ctx._log(name, key, True, fault.corrupt_value, tool.cost)
    return fault.corrupt_value
