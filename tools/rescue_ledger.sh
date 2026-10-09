#!/usr/bin/env bash
# Pull a live matrix's ledger out of its Actions artifact and file it beside
# the experiment that cites it (#46).
#
# WHY THIS EXISTS. `live-gauntlet.yml` uploads runs.jsonl as an artifact and
# nothing ever committed it, so experiments 003 and 007 -- roughly $10 of
# real runs -- exist only as a printed board in console.txt. Experiment 010
# had to answer a question by PARSING that table, because the per-run
# records were not there. Artifacts expire; a committed file does not.
#
# NOT RUNNABLE FROM A CLOUD SESSION. Claude Code's built-in `gh` contacts
# api.github.com only, and GitHub serves artifacts from blob storage, so the
# download is refused by design rather than by a missing flag. Run this
# where the real `gh` CLI is authenticated.
#
#   ./tools/rescue_ledger.sh <run-id> <experiment-dir>
#
# The two outstanding ones, and their deadlines:
#
#   ./tools/rescue_ledger.sh 35009572280 experiments/003-live-m0-gate
#   ./tools/rescue_ledger.sh 35149486157 experiments/007-m0-gate-passed
#
# Artifacts checked 2026-10-09: both alive, expiring 2026-12-14 and
# 2026-12-15.
set -euo pipefail

run_id=${1:?usage: rescue_ledger.sh <run-id> <experiment-dir>}
dest=${2:?usage: rescue_ledger.sh <run-id> <experiment-dir>}

[ -d "$dest" ] || { echo "no such experiment directory: $dest" >&2; exit 2; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

echo "fetching gauntlet-run from run $run_id"
gh run download "$run_id" --name gauntlet-run --dir "$work"

ledger=$(find "$work" -name runs.jsonl -print -quit)
[ -n "$ledger" ] || { echo "no runs.jsonl in the artifact" >&2; exit 1; }

# The fingerprint every record carries is what makes the file attributable.
# A ledger filed next to the wrong experiment is worse than no ledger, so
# this prints what it found rather than trusting the run id alone.
python3 -I - "$ledger" <<'PY'
import collections, json, sys

counts = collections.Counter()
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if line:
        counts[json.loads(line).get("task_fingerprint")] += 1
print(f"  {sum(counts.values())} run(s)")
for fingerprint, n in counts.most_common():
    print(f"  task_fingerprint {fingerprint}: {n}")
PY

cp "$ledger" "$dest/runs.jsonl"
summary=$(find "$work" -name summary.json -print -quit)
[ -n "$summary" ] && cp "$summary" "$dest/summary.json"

echo
echo "wrote $dest/runs.jsonl"
echo "Check the fingerprint above matches the one in $dest/README.md, then commit."
