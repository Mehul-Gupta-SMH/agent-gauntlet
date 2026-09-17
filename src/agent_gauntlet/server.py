"""A local UI for watching a gauntlet run happen.

Stdlib only, on purpose. A dashboard that costs the project a web framework
would be a bad trade for something that runs on localhost for the length of
one matrix.

The contract with the page is narrow and one-directional: the browser posts
an intake, polls `/api/events` by cursor, and draws what it is given. It
never computes a score, a rate or a ranking of its own. Everything it
displays comes from `score_run` and `board.summarize` -- the same functions
the CLI prints and the ledger records -- so the arena cannot drift from the
numbers the project stands behind.

Offline by default: scripted policies, no credentials, no spend. Live runs
are opt-in per request and still go through `preflight`.
"""

from __future__ import annotations

import json
import mimetypes
import threading
import traceback
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from . import architect, board, events, project as projects, runner, usertools
from .interpose import ToolCost
from .ledger import Ledger
from .ledger import errors as ledger_errors
from .matrix import run_matrix
from .spec import GridSpec, Scenario, TaskSpec

UI_DIR = Path(__file__).resolve().parent / "ui"

DEFAULT_MODELS = {"cheap": "anthropic/claude-haiku-4-5",
                  "smart": "anthropic/claude-sonnet-5"}


@dataclass
class Session:
    """One run's worth of state, shared between the worker and the pollers."""

    log: events.EventLog = field(default_factory=events.EventLog)
    status: str = "idle"
    """idle | running | done | failed."""
    error: Optional[str] = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    thread: Optional[threading.Thread] = None
    project_id: Optional[str] = None
    """One project at a time: the matrix saturates the machine, and two
    interleaving would make the per-run timings measure contention."""

    def running(self) -> bool:
        return self.status == "running"


SESSION = Session()

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
BIND_HOST = "127.0.0.1"
"""Set by `serve`. Uploading a Python file means running it in this process,
so that endpoint is available only when the server is bound to loopback --
it has to stay "the operator running their own code on their own machine"
and never become anyone else running it on theirs."""


def uploads_allowed() -> bool:
    return BIND_HOST in LOOPBACK


# --- intake ---------------------------------------------------------------


def catalog() -> dict[str, Any]:
    """What the form may offer, read from the real registries.

    Derived rather than duplicated: a prompt strategy or tool set added to
    `architect` shows up in the UI without anyone remembering to update a
    list here, and one that is removed cannot be selected.
    """
    tools: dict[str, dict[str, Any]] = {}
    for name, members in architect.TOOLSETS.items():
        for tool in members:
            tools.setdefault(tool, {"name": tool, "toolsets": []})
            tools[tool]["toolsets"].append(name)
    return {
        "tools": sorted(tools.values(), key=lambda t: t["name"]),
        "toolsets": {k: sorted(v) for k, v in architect.TOOLSETS.items()},
        "prompts": sorted(architect.PROMPTS),
        "models": DEFAULT_MODELS,
        "sentinel": {"prompt": architect.SENTINEL_PROMPT,
                     "toolset": architect.SENTINEL_TOOLSET},
    }


def toolsets_within(selected: set[str]) -> list[str]:
    """Named tool sets the operator's inventory can actually supply.

    You cannot compete a configuration you have no tools for. Deriving the
    axis from the declared inventory -- rather than letting the form offer
    every tool set regardless -- means the grid the UI runs is one the
    operator could really deploy.
    """
    return sorted(
        name for name, members in architect.TOOLSETS.items()
        if name != architect.SENTINEL_TOOLSET and set(members) <= selected
    )


def task_from_intake(intake: dict[str, Any]) -> TaskSpec:
    """Build a real `TaskSpec` from the form. No display-only shortcuts.

    The statement goes in verbatim -- it is passed identically to every
    variant, which is one of the fairness invariants -- and the result is
    fingerprinted like any other task, so a UI run is as citable as a CLI
    one.
    """
    records = {str(k): int(v) for k, v in (intake.get("records") or {}).items()}
    if not records:
        raise ValueError("the scenario needs at least one record")
    audited = [r for r in (intake.get("audited") or []) if r in records]

    selected = set(intake.get("tools") or [])
    toolsets = toolsets_within(selected)
    if not toolsets:
        raise ValueError(
            "no tool set is fully covered by the selected tools -- pick "
            f"enough tools to supply one of: {sorted(architect.TOOLSETS)}"
        )

    prompts = [p for p in (intake.get("prompts") or []) if p in architect.PROMPTS]
    if not prompts:
        raise ValueError("pick at least one prompt strategy")

    return TaskSpec(
        id=intake.get("id") or "ui_task",
        statement=(intake.get("statement") or "").strip()
        or "Report the total across all records, using the available tools.",
        oracle="full",
        tolerance=int(intake.get("tolerance") or 0),
        fault_tool=intake.get("fault_tool") or "fetch_record",
        acceptable_degradation={"propagation_rate": 0},
        grid=GridSpec(prompts=prompts, toolsets=toolsets),
        scenarios=[Scenario(id="scenario_1", records=records, audited=audited)],
    )


# --- the run --------------------------------------------------------------


def _row(r: board.VariantResult) -> dict[str, Any]:
    """One board row, censored exactly as the CLI censors it.

    `None` travels to the page as `null` and renders as `n/a`. The page has
    no rule for turning a missing measurement into a zero, because it is
    never given the chance to.
    """
    return {
        "variant_id": r.variant_id,
        "label": r.label,
        "factors": r.factors,
        "n_runs": r.n_runs,
        "graded": r.graded,
        "quality": r.quality,
        "accuracy": r.accuracy,
        "clean_quality": r.clean_quality,
        "faulted_quality": r.faulted_quality,
        "propagation_rate": r.propagation_rate,
        "propagation_unmeasured": r.propagation_unmeasured,
        "detection_rate": r.detection_rate,
        "repair_rate": r.repair_rate,
        "false_alarm_rate": r.false_alarm_rate,
        "median_detect_latency": r.median_detect_latency,
        "material_calls": r.material_calls,
        "redundant_material_calls": r.redundant_material_calls,
        "n_errored": r.n_errored,
        "cost_per_run": r.cost_per_run,
        "gated": r.gated,
        "over_harm_budget": r.over_harm_budget,
        "harm_budget": r.harm_budget,
        "n_propagation_undecidable": r.n_propagation_undecidable,
        "is_sentinel": r.is_sentinel,
    }


def _worker(session: Session, intake: dict[str, Any]) -> None:
    try:
        task = task_from_intake(intake)
        repeats = max(1, min(int(intake.get("repeats") or 2), 10))
        seeds = max(1, min(int(intake.get("seeds") or 2), 10))

        with TemporaryDirectory() as tmp:
            variants = architect.generate(
                out_dir=Path(tmp) / "variants", task=task, models=DEFAULT_MODELS,
            )
            session.log.emit(
                "intake.accepted",
                task=task.id,
                statement=task.statement,
                fingerprint=task.fingerprint(),
                toolsets=task.grid.toolsets,
                prompts=task.grid.prompts,
                records=task.scenarios[0].records,
                audited=task.scenarios[0].audited_ids,
                expected=task.scenarios[0].expected_total,
                seeds=seeds,
                repeats=repeats,
                # The whole plan up front, so the progress counter never
                # shows a total that grows as seeds arrive.
                total_runs=len(variants) * len(task.scenarios) * repeats * seeds * 2,
            )
            ledger = Ledger(Path(tmp) / "runs.jsonl")
            records: list = []
            for i in range(seeds):
                records.extend(run_matrix(
                    task=task, variants=variants, ledger=ledger,
                    base_seed=f"seed{i}", repeats=repeats, offline=True,
                ))

            results = board.summarize(records)
            held = board.held_out_winner(records)
            session.log.emit(
                "board",
                rows=[_row(r) for r in results],
                errored=len(ledger_errors(records)),
                total=len(records),
                # The selection score is never the reported score (#20).
                # If the split could not be formed, the page is told so
                # rather than being handed the optimistic number.
                # Why there is no winner matters: a tie is a real answer
                # (#11), while one seed simply cannot be split. Telling the
                # page "run more seeds" for a tie would be advice that does
                # not fix anything.
                winner_reason=(
                    None if held is not None
                    else "one seed -- nothing to hold the winner out on"
                    if len({r.base_seed for r in records}) < 2
                    else "The selection half is tied at the top, and a tie is "
                         "a real answer rather than a winner."
                ),
                winner=None if held is None else {
                    "variant_id": held.variant_id,
                    "selection_score": held.selection_score,
                    "holdout_score": held.holdout_score,
                    "held_up": held.held_up,
                    "holdout_rank": held.holdout_rank,
                    "n_candidates": held.n_candidates,
                },
            )
        with session.lock:
            session.status = "done"
    except Exception as exc:  # surfaced to the page, not swallowed
        with session.lock:
            session.status = "failed"
            session.error = f"{type(exc).__name__}: {exc}"
        session.log.emit("failed", error=session.error,
                         trace=traceback.format_exc()[-2000:])
    finally:
        events.attach(None)


def _project_worker(session: Session, p: projects.Project) -> None:
    """Calibrate, run, score. Same matrix, same board as every fixture."""
    try:
        p.stage, p.error = "running", None
        p.save()
        session.log.emit(
            "intake.accepted", task=p.id, statement=p.statement,
            fingerprint="(after calibration)",
            tools=[t.name for t in p.tools], fault_tool=p.fault_tool,
            seeds=p.seeds, repeats=p.repeats,
            total_runs=(len(p.models) * len(p.prompts)
                        * len(runner.toolsets(p)) * p.repeats * p.seeds * 2),
        )
        ledger = Ledger(p.dir / "runs.jsonl")
        budget = runner.Budget(limit_usd=p.budget_usd) if not p.offline else None
        try:
            records = runner.run(p, ledger, budget=budget)
        except runner.BudgetExceeded as stop:
            # Not a failure: the runs that completed are real, scored and in
            # the ledger. Reporting them with the ceiling named beats
            # throwing away a partial matrix the operator paid for.
            records = ledger.records()
            session.log.emit("budget.stopped", reason=str(stop),
                             spent=budget.spent_usd, limit=budget.limit_usd)
        if not p.offline and budget is not None:
            session.log.emit("spend", usd=budget.spent_usd, runs=budget.runs,
                             limit=budget.limit_usd)
        results = board.summarize(records, harm_budget=0)
        held = board.held_out_winner(records)
        graded = all(r.graded for r in results)
        session.log.emit(
            "board",
            rows=[_row(r) for r in results],
            errored=len(ledger_errors(records)),
            total=len(records),
            graded=graded,
            # Without a label there is a gate but no leaderboard, and saying
            # so beats printing a ranking of numbers that do not mean what
            # the column heading says.
            winner_reason=(
                None if held is not None
                else "This project has no expected answer, so propagation is "
                     "gated but nothing can be ranked by correctness. Add the "
                     "right answer for a scenario to get a leaderboard."
                if not graded
                else "one seed -- nothing to hold the winner out on"
                if p.seeds < 2
                else "The selection half is tied at the top, and a tie is a "
                     "real answer rather than a winner."
            ),
            winner=None if held is None else {
                "variant_id": held.variant_id,
                "selection_score": held.selection_score,
                "holdout_score": held.holdout_score,
                "held_up": held.held_up,
                "holdout_rank": held.holdout_rank,
                "n_candidates": held.n_candidates,
            },
        )
        p.stage = "done"
        p.save()
        with session.lock:
            session.status = "done"
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        p.stage, p.error = "failed", detail
        p.save()
        with session.lock:
            session.status, session.error = "failed", detail
        session.log.emit("failed", error=detail,
                         trace=traceback.format_exc()[-2000:])
    finally:
        events.attach(None)


def start_project(session: Session, p: projects.Project) -> None:
    blockers = p.blockers()
    if blockers:
        raise ValueError("; ".join(blockers))
    with session.lock:
        if session.running():
            raise RuntimeError("a run is already in progress")
        session.status, session.error = "running", None
        session.project_id = p.id
    session.log.clear()
    events.attach(session.log)
    session.thread = threading.Thread(
        target=_project_worker, args=(session, p), daemon=True
    )
    session.thread.start()


def start_run(session: Session, intake: dict[str, Any]) -> None:
    with session.lock:
        if session.running():
            raise RuntimeError("a run is already in progress")
        session.status = "running"
        session.error = None
    session.log.clear()
    events.attach(session.log)
    session.thread = threading.Thread(
        target=_worker, args=(session, intake), daemon=True
    )
    session.thread.start()


# --- projects -------------------------------------------------------------


def project_view(p: projects.Project) -> dict[str, Any]:
    """Everything the wizard needs, and no secret.

    Credential **names** travel, with whether each one is currently present
    in the environment. The value is never read here or anywhere else, so
    there is nothing for this payload to leak.
    """
    missing = set(p.missing_credentials())
    return {
        "id": p.id, "name": p.name, "created_at": p.created_at,
        "statement": p.statement, "stage": p.stage, "error": p.error,
        "tools": [
            {
                "name": t.name, "description": t.description,
                "cost": t.cost.value, "module": t.module, "function": t.function,
                "described": t.described,
                "arguments": [a.model_dump() for a in t.arguments],
                "credentials": [
                    {"name": n, "present": n not in missing}
                    for n in t.requires_credentials
                ],
                "rows": len(t.table),
            }
            for t in p.tools
        ],
        "models": [m.model_dump() for m in p.models],
        "prompts": p.prompts,
        "fault_tool": p.fault_tool,
        "scenarios": [sc.model_dump() for sc in p.scenarios],
        "repeats": p.repeats, "seeds": p.seeds, "offline": p.offline,
        "target": p.target, "budget_usd": p.budget_usd,
        "estimate_usd": runner.estimate_usd(p),
        "blockers": p.blockers(),
        "missing_credentials": sorted(missing),
        "material": p.material_tools(),
        "uploads_allowed": uploads_allowed(),
    }


def apply_patch(p: projects.Project, patch: dict[str, Any]) -> projects.Project:
    """Merge one wizard step into the project.

    Field by field rather than a wholesale replace, so a step that does not
    mention tools cannot silently drop the operator's uploads.
    """
    for field in ("name", "statement", "fault_tool", "stage", "target"):
        if field in patch:
            setattr(p, field, patch[field] or "")
    if "offline" in patch:
        p.offline = bool(patch["offline"])
    if "budget_usd" in patch:
        raw = patch["budget_usd"]
        p.budget_usd = None if raw in (None, "") else max(0.0, float(raw))
    for field in ("repeats", "seeds"):
        if field in patch:
            setattr(p, field, max(1, min(int(patch[field] or 1), 10)))
    if "prompts" in patch:
        p.prompts = [x for x in patch["prompts"] if x in runner.POLICIES
                     and x != "sentinel"]
    if "models" in patch:
        p.models = [projects.ModelChoice(**m) for m in patch["models"]]
    if "scenarios" in patch:
        p.scenarios = [projects.Scenario(**sc) for sc in patch["scenarios"]]
    if "tools" in patch:
        p.tools = [usertools.UserTool(**t) for t in patch["tools"]]
    p.save()
    return p


def store_upload(p: projects.Project, filename: str, source: str) -> dict[str, Any]:
    """Save an uploaded file and report what is in it -- without running it.

    The functions are listed by parsing, so the operator chooses which one
    is the tool from the file's text. Anything at module level that will run
    on import is reported as a warning, because picking a function from this
    file also runs everything beside it.
    """
    if not uploads_allowed():
        raise PermissionError(
            f"uploads are disabled because this server is bound to {BIND_HOST}, "
            "not loopback. Uploading a file means executing it here."
        )
    name = projects.slug(Path(filename).stem, "tool") + ".py"
    try:
        found = usertools.discover(source)
        effects = usertools.top_level_effects(source)
    except SyntaxError as exc:
        raise ValueError(f"{filename} does not parse: {exc}") from exc
    if not found:
        raise ValueError(f"{filename} defines no top-level functions")

    p.tools_dir.mkdir(parents=True, exist_ok=True)
    (p.tools_dir / name).write_text(source, encoding="utf-8")
    return {
        "module": name,
        "functions": [f.model_dump() for f in found],
        "import_effects": effects,
    }


# --- http -----------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "agent-gauntlet"

    def log_message(self, fmt: str, *args: Any) -> None:
        pass  # the console belongs to the run, not to request noise

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # A localhost dev surface. No caching, so an edited asset shows up
        # on reload rather than needing a hard refresh.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path

        if route in ("/", "/index.html"):
            return self._file(UI_DIR / "index.html")
        if route == "/api/catalog":
            return self._json(200, {
                **catalog(),
                "policies": [k for k in sorted(runner.POLICIES) if k != "sentinel"],
                "costs": [c.value for c in ToolCost],
                "uploads_allowed": uploads_allowed(),
                "bind_host": BIND_HOST,
            })
        if route == "/api/projects":
            return self._json(200, {"projects": projects.listing(),
                                    "uploads_allowed": uploads_allowed()})
        if route.startswith("/api/projects/"):
            try:
                p = projects.Project.load(route.split("/")[3])
            except (OSError, ValueError):
                return self._json(404, {"error": "no such project"})
            return self._json(200, project_view(p))
        if route == "/api/events":
            cursor = int((parse_qs(parsed.query).get("since") or ["0"])[0])
            with SESSION.lock:
                status, error = SESSION.status, SESSION.error
            return self._json(200, {
                "status": status,
                "error": error,
                "cursor": SESSION.log.cursor,
                "events": [e.as_json() for e in SESSION.log.since(cursor)],
            })

        # Static assets, resolved under UI_DIR and nowhere else.
        candidate = (UI_DIR / route.lstrip("/")).resolve()
        if candidate.is_file() and UI_DIR.resolve() in candidate.parents:
            return self._file(candidate)
        return self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            return self._json(400, {"error": f"bad JSON: {exc}"})

        if route == "/api/run":                      # the quick fixture path
            try:
                start_run(SESSION, body)
            except RuntimeError as exc:
                return self._json(409, {"error": str(exc)})
            return self._json(202, {"status": "running"})

        if route == "/api/projects":
            p = projects.new(body.get("name") or "untitled")
            return self._json(201, project_view(p))

        parts = route.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "projects":
            try:
                p = projects.Project.load(parts[2])
            except (OSError, ValueError):
                return self._json(404, {"error": "no such project"})
            action = parts[3] if len(parts) > 3 else ""

            if action == "":
                return self._json(200, project_view(apply_patch(p, body)))

            if action == "upload":
                try:
                    found = store_upload(p, body.get("filename") or "tool.py",
                                         body.get("source") or "")
                except PermissionError as exc:
                    return self._json(403, {"error": str(exc)})
                except ValueError as exc:
                    return self._json(400, {"error": str(exc)})
                return self._json(200, found)

            if action == "run":
                try:
                    start_project(SESSION, p)
                except ValueError as exc:
                    return self._json(400, {"error": str(exc)})
                except RuntimeError as exc:
                    return self._json(409, {"error": str(exc)})
                return self._json(202, {"status": "running", "project": p.id})

            if action == "delete":
                p.delete()
                return self._json(200, {"deleted": p.id})

        return self._json(404, {"error": "not found"})

    def _file(self, path: Path) -> None:
        ctype, _ = mimetypes.guess_type(path.name)
        self._send(200, path.read_bytes(), ctype or "application/octet-stream")


def serve(host: str = "127.0.0.1", port: int = 8420) -> None:
    global BIND_HOST
    BIND_HOST = host
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"gauntlet ui   http://{host}:{port}")
    print(f"projects      {projects.root()}")
    print("              offline -- scripted policies, no spend")
    if uploads_allowed():
        print("              uploaded tools RUN IN THIS PROCESS, on this machine")
    else:
        print(f"              tool upload DISABLED: bound to {host}, not loopback")
    print("              ctrl-c to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
