"""The child process that runs an operator's own tool.

Calibration is the only place code the operator wrote executes at all, and
until now it executed *in the server process* -- with that process's
environment, which is where the provider keys live. The guard on uploads
was about who could reach the port; nothing limited what an upload could
do once it was there.

So calibration happens here, in a separate process, started with an
environment that has no credentials in it. What this buys, precisely:

* **No credential exposure.** The child's environment is built from an
  allowlist, not filtered by a denylist -- a denylist protects the
  variables somebody remembered to name.
* **A wall clock.** A tool that loops is a cost incident when it is
  calling a paid API, and the parent kills it.
* **No import in the parent.** `usertools.load` now runs only here, so the
  server never imports the operator's file.

What it does NOT buy, and the README says so in these words: this is a
process boundary, not a sandbox. There is no seccomp, no namespace, no
filesystem or network restriction. A tool run here can still open sockets,
read files the user can read, and spawn its own children. Real isolation
is a container or a WASM runtime, and is the rest of #12.

The result goes to a file rather than stdout, because the tool's own
`print` at import time shares stdout with us -- the bundled test fixture
does exactly that, and a result channel a tool can corrupt by being chatty
is not a result channel.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def run(spec: dict[str, Any]) -> dict[str, Any]:
    """Call the tool once per input, `repeats` times, and compare."""
    from .usertools import key_for, load

    fn = load(Path(spec["module_path"]), spec["function"])
    table: dict[str, Any] = {}
    for args in spec["inputs"]:
        key = key_for(args)
        seen = []
        for _ in range(max(1, int(spec.get("repeats", 2)))):
            seen.append(fn(*args))
        if any(v != seen[0] for v in seen[1:]):
            return {
                "ok": False,
                "kind": "nondeterministic",
                "args": args,
                "first": repr(seen[0]),
                "second": repr(seen[1]),
            }
        try:
            # Checked here rather than at the boundary so the message can
            # name the call that produced it. The board compares numbers;
            # a value that cannot cross a pipe cannot be a truth for it.
            json.dumps(seen[0])
        except (TypeError, ValueError):
            return {
                "ok": False,
                "kind": "unserializable",
                "args": args,
                "returned": type(seen[0]).__name__,
            }
        table[key] = seen[0]
    return {"ok": True, "table": table}


def main() -> int:
    out = Path(sys.argv[1])
    spec = json.loads(sys.stdin.read())
    try:
        result = run(spec)
    except Exception as exc:                      # the tool's own failure
        result = {"ok": False, "kind": "raised",
                  "error": f"{type(exc).__name__}: {exc}"}
    out.write_text(json.dumps(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
