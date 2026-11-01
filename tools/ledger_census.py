#!/usr/bin/env python3
"""Report what a ledger contains, so it can be attributed before it is filed.

Split out of `rescue_ledger.sh` and `rescue-ledgers.yml` rather than inlined
as `python3 -c`, for one reason: both callers run it against a file that was
just extracted from an Actions artifact, and an archive is untrusted data
until something has looked at it. A real file can be invoked with `-I` from
a directory the archive did not write to; a `-c` one-liner has to be quoted
into a YAML block scalar, where the quoting is the bug nobody sees until it
runs in CI.

It prints, and judges nothing. The caller decides whether the fingerprint it
reports is the one it wanted -- "a ledger filed next to the wrong experiment
is worse than no ledger" is the whole reason this file exists, and a
comparison made here would hide the expectation inside the tool that reports
the fact.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys


def _rows(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"line {number} is not JSON: {exc}", file=sys.stderr)
                raise SystemExit(1)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger")
    parser.add_argument(
        "--errors",
        action="store_true",
        help="print the `error` strings instead of the fingerprint census. "
        "That field is the only one carrying text from outside this repo.",
    )
    args = parser.parse_args()

    rows = _rows(args.ledger)
    if not rows:
        print("empty ledger", file=sys.stderr)
        return 1

    if args.errors:
        errors = [row["error"] for row in rows if row.get("error")]
        for error in errors:
            print("  " + error[:200].replace("\n", " "))
        print(f"  ({len(errors)} of {len(rows)} record(s) carried an error)")
        return 0

    counts = collections.Counter(row.get("task_fingerprint") for row in rows)
    print(f"runs {len(rows)}")
    for fingerprint, n in counts.most_common():
        print(f"fingerprint {fingerprint}: {n}")
    # Last, on its own line, so a shell can `sed -n 's/^dominant //p'` it
    # without depending on the census above staying one line long.
    print(f"dominant {counts.most_common(1)[0][0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
