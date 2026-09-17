"""Getting credentials to a live run, without letting them leak.

The project's standing rule is that a *spec* never carries a secret: tools
and variants declare environment variable NAMES and the harness checks
presence. That rule is about what gets written down and shared -- the
project file, the ledger, a generated `common/` folder, a screenshot of the
board. It was never a reason the operator should have no way to supply a
value at all, and without one the live path cannot be exercised from the UI.

So there are two routes here, and both keep the rule intact:

* **A `.env` file.** The value never touches the browser. Read at startup
  into this process's environment and nothing else. This is the route to
  prefer, and the one CI and scripts already use.
* **Typing it into the page.** Held in this process's memory for as long as
  the server runs. Never written to disk unless explicitly asked, never
  returned by any endpoint, never placed in a URL, and never stored in the
  project file.

What is deliberately absent: any code path that reads a value back out. The
only questions anything can ask are "is this name set?" and "where did it
come from?" -- so a screenshot, a bug report, or a `project.json` committed
by accident has nothing in it to lose.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

VALID_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
"""Conventional environment variable names only.

Not decoration: the name is interpolated into messages and written into
`.env`, and a name containing a newline or an `=` could forge a second
assignment in a file that is later read back as trusted configuration.
"""


class BadSecretName(ValueError):
    pass


def check_name(name: str) -> str:
    name = (name or "").strip()
    if not VALID_NAME.match(name):
        raise BadSecretName(
            f"{name!r} is not a usable environment variable name -- expected "
            "upper-case letters, digits and underscores, 2 to 64 characters"
        )
    return name


# --- .env -----------------------------------------------------------------


def parse_env(text: str) -> dict[str, str]:
    """Parse a `.env` file.

    Deliberately literal: `KEY=value`, optional `export`, `#` comments,
    and quotes stripped when they wrap the whole value. No shell expansion,
    no command substitution, no `$VAR` interpolation -- a credentials file
    is data, and treating it as a program is how a config file becomes an
    execution path.
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if not VALID_NAME.match(name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[name] = value
    return out


def load_env_file(path: Path, *, override: bool = False) -> list[str]:
    """Load a `.env` into this process. Returns the NAMES it set.

    Does not override what is already in the environment unless asked: a
    value exported in the shell that started the server is the more
    deliberate of the two, and silently shadowing it would make "which key
    did that run use?" unanswerable.
    """
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    applied: list[str] = []
    for name, value in parse_env(text).items():
        if override or not os.environ.get(name):
            os.environ[name] = value
            applied.append(name)
    return applied


def candidate_files(project_root: Path, explicit: Optional[str] = None) -> list[Path]:
    """Where a `.env` is looked for, nearest-first.

    `GAUNTLET_ENV_FILE` wins, then the working directory, then the project
    store. The working directory comes first because it is the one the
    operator is looking at.
    """
    paths = []
    if explicit:
        paths.append(Path(explicit).expanduser())
    paths.append(Path.cwd() / ".env")
    paths.append(project_root / ".env")
    seen, unique = set(), []
    for p in paths:
        key = str(p.resolve()) if p.parent.exists() else str(p)
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


# --- the in-memory store --------------------------------------------------


@dataclass
class Store:
    """Names this session put into the environment, and where each came from.

    Values live in `os.environ` and nowhere else in this module. There is no
    dictionary of them here to be serialized by accident.
    """

    sources: dict[str, str] = field(default_factory=dict)
    """name -> "session" | "<path to the .env it came from>"."""

    def set(self, name: str, value: str) -> str:
        name = check_name(name)
        if not value:
            raise ValueError(f"{name} was given an empty value")
        os.environ[name] = value
        self.sources[name] = "session"
        return name

    def clear(self, name: str) -> None:
        name = check_name(name)
        os.environ.pop(name, None)
        self.sources.pop(name, None)

    def note_file(self, names: list[str], path: Path) -> None:
        for name in names:
            self.sources[name] = str(path)

    def status(self, names: list[str]) -> list[dict[str, object]]:
        """Presence and provenance. Never the value.

        `source` is "environment" for anything this server did not set
        itself, which covers a variable exported in the operator's shell.
        """
        out = []
        for name in sorted(set(names)):
            present = bool(os.environ.get(name))
            out.append({
                "name": name,
                "present": present,
                "source": self.sources.get(name, "environment" if present else None),
            })
        return out


def write_env_file(path: Path, name: str, value: str) -> None:
    """Persist one variable to a `.env`, replacing any existing line.

    Written 0600 and only where the caller chose. The projects store also
    carries a `.gitignore` of `*` (see `project.root`), so a store that
    happens to sit inside a repository cannot commit this by accident.
    """
    name = check_name(name)
    if "\n" in value or "\r" in value:
        raise ValueError(f"{name} value contains a newline and cannot be stored")

    lines = []
    if path.is_file():
        lines = [
            ln for ln in path.read_text(encoding="utf-8").splitlines()
            if not ln.strip().removeprefix("export ").lstrip().startswith(f"{name}=")
        ]
    lines.append(f"{name}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # a filesystem without permissions is not a reason to fail
