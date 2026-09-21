"""Calibration in a child process: what it buys, and what it does not (#12).

Calibration is the only place code the operator wrote runs at all, and it
used to run *in the server process* -- with that process's environment,
which is where the provider keys are. The upload guard was about who could
reach the port; nothing limited what an upload could do once it was there.

The claims these pin, in order of how badly a regression would matter:

1. The tool cannot read the operator's credentials. By allowlist, because
   a denylist protects the variables somebody remembered to name.
2. A tool that never returns is killed rather than left running. A loop
   against a paid API is a cost incident.
3. The parent never imports the operator's file at all.
4. A chatty tool cannot corrupt the result channel.

And the last test is the honest limit: this is a process boundary, NOT a
sandbox. A project whose argument is "do not trust an output because it
looks reassuring" has no business letting its own security note read
better than its code.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from agent_gauntlet import project as projects, usertools
from agent_gauntlet.usertools import CalibrationError, UserTool


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAUNTLET_HOME", str(tmp_path / "projects"))
    return tmp_path


@pytest.fixture
def project(home):
    return projects.new("isolation")


def _tool(project, source: str, function: str = "probe") -> UserTool:
    project.tools_dir.mkdir(parents=True, exist_ok=True)
    (project.tools_dir / "t.py").write_text(source, encoding="utf-8")
    usertools._LOADED.clear()
    return UserTool(name=function, module="t.py", function=function)


def _calibrate(project, tool, inputs=None, **kw):
    return usertools.calibrate(
        tool, inputs or [["x"]], project_dir=project.dir, repeats=1, **kw)


# --- 1. the credentials -----------------------------------------------------


def test_a_tool_cannot_read_the_operators_provider_key(project, monkeypatch):
    """The exposure this exists to remove. Uploaded code ran in the process
    holding the keys, so a tool that read one had it."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    tool = _tool(project, "import os\n"
                          "def probe(x):\n"
                          "    return len(os.environ.get('ANTHROPIC_API_KEY', ''))\n")
    table = _calibrate(project, tool)
    assert list(table.values()) == [0], "the child saw a credential"


def test_the_environment_is_an_allowlist_not_a_denylist(project, monkeypatch):
    """A denylist protects the variables somebody thought to name. This one
    is not named anywhere and must still be invisible."""
    monkeypatch.setenv("SOME_INTERNAL_TOKEN_NOBODY_LISTED", "hunter2")
    tool = _tool(project, "import os\n"
                          "def probe(x):\n"
                          "    return sorted(os.environ)\n")
    seen = list(_calibrate(project, tool).values())[0]
    assert "SOME_INTERNAL_TOKEN_NOBODY_LISTED" not in seen
    # The allowlist governs what the parent PASSES. CPython itself adds a
    # couple of locale variables on the way in (PEP 538), which is worth
    # knowing rather than papering over -- so they are named here instead
    # of loosening the assertion to nothing.
    INTERPRETER_ADDS = {"LC_CTYPE", "PYTHONHASHSEED"}
    assert set(seen) <= set(usertools.ENV_ALLOWLIST) | INTERPRETER_ADDS


def test_the_child_still_gets_what_a_tool_legitimately_needs(project):
    """Stripped, not crippled: a tool that shells out or writes a temp file
    is doing something ordinary."""
    tool = _tool(project, "import os\n"
                          "def probe(x):\n"
                          "    return bool(os.environ.get('PATH'))\n")
    assert list(_calibrate(project, tool).values()) == [True]
    assert "PATH" in usertools.child_env()
    assert not any(k.endswith("_API_KEY") for k in usertools.child_env())


# --- 2. the wall clock ------------------------------------------------------


def test_a_tool_that_never_returns_is_killed(project):
    """A loop against a paid API is a cost incident, so the ceiling is in
    the contract rather than in a linter."""
    tool = _tool(project, "import time\n"
                          "def probe(x):\n"
                          "    time.sleep(30)\n"
                          "    return 1\n")
    with pytest.raises(CalibrationError, match="did not finish calibrating"):
        _calibrate(project, tool, timeout=1.5)


def test_a_tool_that_kills_its_own_process_is_reported(project):
    """`os._exit` skips every `finally` there is, so nothing the child
    writes can be relied on -- the parent has to notice the silence."""
    tool = _tool(project, "import os\n"
                          "def probe(x):\n"
                          "    os._exit(0)\n")
    with pytest.raises(CalibrationError, match="produced no result"):
        _calibrate(project, tool)


# --- 3. the parent stays clean ---------------------------------------------


def test_the_parent_process_never_imports_the_operators_file(project):
    """`usertools.load` now runs only in the child. If this regresses, the
    server is importing uploaded code again and everything above is
    decoration."""
    marker = project.dir / "imported-here.txt"
    tool = _tool(project, f"open({str(marker)!r}, 'a').write('x')\n"
                          "def probe(x):\n"
                          "    return 7\n")
    before = {m for m in sys.modules if m.startswith("gauntlet_tool_")}
    assert list(_calibrate(project, tool).values()) == [7]

    # It ran -- in the child.
    assert marker.exists()
    # And not here.
    assert {m for m in sys.modules if m.startswith("gauntlet_tool_")} == before
    assert usertools._LOADED == {}


# --- 4. the result channel --------------------------------------------------


def test_a_chatty_tool_cannot_corrupt_the_result(project):
    """The bundled fixture prints on import. A result channel a tool can
    spoil by being noisy is not a result channel, so the child writes to a
    file and leaves stdout to the tool."""
    tool = _tool(project, 'print(\'{"ok": false, "kind": "raised"}\')\n'
                          "import sys\n"
                          "sys.stdout.write('more noise\\n')\n"
                          "def probe(x):\n"
                          "    print('and some from the call')\n"
                          "    return 42\n")
    assert list(_calibrate(project, tool).values()) == [42]


def test_a_value_that_cannot_cross_the_boundary_is_refused(project):
    """The board compares numbers. A value that cannot be serialized cannot
    be a calibrated truth, and the refusal names the call."""
    tool = _tool(project, "class Thing:\n    pass\n"
                          "def probe(x):\n"
                          "    return Thing()\n")
    with pytest.raises(CalibrationError, match="cannot cross a process boundary"):
        _calibrate(project, tool)


def test_a_tool_that_raises_still_says_why(project):
    """The message is the operator's only debugging surface for code that
    ran somewhere they cannot see."""
    tool = _tool(project, "def probe(x):\n"
                          "    raise KeyError('no such applicant')\n")
    with pytest.raises(CalibrationError, match="KeyError"):
        _calibrate(project, tool)


def test_non_determinism_is_still_caught_across_the_boundary(project):
    """The check that was there before, unbroken by moving processes: its
    noise would be scored as the agent's."""
    tool = _tool(project, "import itertools\n_n = itertools.count()\n"
                          "def probe(x):\n"
                          "    return next(_n)\n")
    with pytest.raises(CalibrationError, match="not deterministic"):
        usertools.calibrate(tool, [["x"]], project_dir=project.dir, repeats=2)


# --- the honest limit -------------------------------------------------------


def test_this_is_a_process_boundary_and_not_a_sandbox(project, tmp_path):
    """Pinned deliberately, as a fact rather than an aspiration.

    There is no seccomp, no namespace, no filesystem restriction. The child
    runs with `cwd` in a scratch directory so *relative* writes land
    somewhere disposable, and an absolute path goes wherever the user could
    write anyway. Documenting isolation the code does not have is exactly
    the reassuring-looking falsehood this project exists to catch, so the
    limit gets a test of its own and the README says the same thing in
    words.
    """
    escaped = tmp_path / "outside.txt"
    tool = _tool(project, "def probe(path):\n"
                          "    open(path, 'w').write('I am not contained')\n"
                          "    return 1\n")
    assert list(_calibrate(project, tool, [[str(escaped)]]).values()) == [1]
    assert escaped.read_text() == "I am not contained"
