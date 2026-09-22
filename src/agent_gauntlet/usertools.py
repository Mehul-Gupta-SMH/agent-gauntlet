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
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
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


CALIBRATION_TIMEOUT = 60.0
"""Wall clock for one tool's whole calibration, in seconds.

A tool that loops is a cost incident when it is calling a paid API, and
the ceiling belongs in the contract rather than in a linter (#12). Generous
because calibration is once per input before the matrix, not per run: eight
calls, not seventy-two.
"""

ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP",
                 "SYSTEMROOT", "COMSPEC", "PATHEXT")
"""What the child is allowed to see.

An allowlist, not a denylist: a denylist protects the variables somebody
remembered to name, and the whole point is that the operator's provider
keys -- whatever they are called -- are not in that process.
"""


def child_env() -> dict[str, str]:
    """The environment calibration runs in. No credentials, by construction."""
    return {k: v for k, v in os.environ.items() if k in ENV_ALLOWLIST}


def calibrate(
    tool: UserTool,
    inputs: list[list[Any]],
    *,
    project_dir: Path,
    repeats: int = 2,
    timeout: float = CALIBRATION_TIMEOUT,
) -> dict[str, Any]:
    """Call an uploaded tool once per input and record what it returns.

    Called `repeats` times per input and compared. A function that does not
    agree with itself has no truth for the board to be scored against, and
    the run is refused rather than averaged -- the disagreement would
    otherwise be attributed to whichever agent happened to see it.

    Runs in a **separate process** with an environment built from an
    allowlist, so the operator's code never sees the operator's
    credentials, and the server never imports it (#12). A process boundary
    is not a sandbox and this docstring will not pretend otherwise -- see
    `_calibrate` for exactly what it does and does not buy.
    """
    if tool.described:
        return dict(tool.table)

    module_path = project_dir / "tools" / tool.module
    spec = {
        "module_path": str(module_path.resolve()),
        "function": tool.function,
        "inputs": inputs,
        "repeats": max(1, repeats),
    }
    with TemporaryDirectory() as scratch:
        result_path = Path(scratch) / "result.json"
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "agent_gauntlet._calibrate",
                 str(result_path)],
                input=json.dumps(spec),
                text=True, capture_output=True, timeout=timeout,
                env=child_env(),
                # Relative writes land in a directory that is about to be
                # deleted. A nudge, not a guarantee: nothing here stops an
                # absolute path, and claiming otherwise would be the kind
                # of reassuring-looking falsehood this project exists to
                # catch.
                cwd=scratch,
            )
        except subprocess.TimeoutExpired:
            raise CalibrationError(
                f"{tool.name} did not finish calibrating within {timeout:.0f}s "
                f"and was killed. A tool that loops is a cost incident when it "
                f"is calling a paid API, so the run is refused rather than "
                f"left running."
            ) from None

        if not result_path.exists():
            raise CalibrationError(
                f"calibrating {tool.name} produced no result "
                f"(exit {proc.returncode}). The process died before it could "
                f"report -- a hard crash, a `sys.exit`, or an `os._exit` in "
                f"the tool or its imports."
                + _tail(proc.stderr)
            )
        outcome = json.loads(result_path.read_text(encoding="utf-8"))

    if outcome.get("ok"):
        return outcome["table"]

    kind = outcome.get("kind")
    call = f"{tool.name}({', '.join(map(repr, outcome.get('args', [])))})"
    if kind == "nondeterministic":
        raise CalibrationError(
            f"{call} is not deterministic: returned {outcome['first']} then "
            f"{outcome['second']}. The gauntlet grades by comparing a faulted "
            f"run against a known truth, and a tool that disagrees with itself "
            f"has none -- its noise would be scored as the agent's."
        )
    if kind == "unserializable":
        raise CalibrationError(
            f"{call} returned a {outcome['returned']}, which cannot be a "
            f"calibrated truth: the board compares numbers, and a value that "
            f"cannot cross a process boundary cannot be one."
        )
    raise CalibrationError(
        f"{tool.name} raised while calibrating: {outcome.get('error')}"
        + _tail(proc.stderr)
    )


def _tail(text: str, lines: int = 8) -> str:
    """The end of the child's stderr, when there is any."""
    kept = [ln for ln in (text or "").strip().splitlines() if ln][-lines:]
    return ("\n\nits output:\n  " + "\n  ".join(kept)) if kept else ""


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

    if fault.kind is FaultKind.INSTRUCTION:
        # The value is untouched; what is corrupted is the authority
        # attached to it. A project has no separate annotation tool, so the
        # directive rides on the operator's own tool -- exactly as a
        # compromised upstream would return it, one string with nothing
        # marking which half is data and which is a command.
        value = tool.table[key]
        poisoned = f"{value}\n\n{fault.directive}"
        ctx.injected_directives.append(fault.directive or "")
        ctx._log(name, key, True, poisoned, tool.cost,
                 kind=fault.kind.value,
                 shape=fault.shape.value if fault.shape else None)
        return poisoned

    if fault.kind is FaultKind.TIMEOUT:
        ctx._log(name, key, True, "timeout", tool.cost,
                 kind=fault.kind.value)
        raise ToolTimeout(f"{name}{tuple(args)} timed out")

    ctx._log(name, key, True, fault.corrupt_value, tool.cost,
             kind=fault.kind.value)
    return fault.corrupt_value
