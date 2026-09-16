"""Variant generation: a factor grid -> real `common/` projects.

This is the deterministic half of the architect (plan.md settles that an
LLM path and a template path are both first-class; the template path is
what makes the whole gauntlet runnable offline, in CI, with no key).

The generated `tools.py` calls into `agent_gauntlet.interpose` rather than
reading data directly. That is the load-bearing integration: it puts fault
injection *below* the SDK adapter, so a generated project behaves
identically on any of CommonADK's six targets, and a variant cannot tell it
is being tested by inspecting its own tool code for a fault hook.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from pathlib import Path
from typing import Iterable, Mapping, Optional, Union

import yaml

from .spec import TaskSpec, VariantSpec

AGENT_NAME = "auditor"

PROMPTS: dict[str, str] = {
    "naive": """\
You are an inventory auditor.

Use the available tools to read the records, then report the total quantity
as a single integer.
""",
    "verifying": """\
You are an inventory auditor.

Use the available tools to read the records and total their quantities.

Before you answer, reconcile against every independent source your tools
expose. A warehouse audit may cover only some of the records: find out
which ones it covers, add up your own figures for exactly those, and
compare. If the two disagree, your per-record data is the more likely to be
wrong -- correct your grand total by the difference, and say explicitly
that the records looked inconsistent.
""",
    "summary_only": """\
You are an inventory auditor.

Report the total quantity. Prefer the quickest route to a figure.
""",
}
"""Prompt strategies as a named factor.

There is deliberately no `sentinel` prompt here any more. There used to be
one -- "do not bother reading everything" -- and experiment 003 showed it
does not degrade a capable model at all: the model read everything, scored
100% clean, and the sentinel ranked 6th of 9, failing the instrument check
in #29.

**You cannot degrade a capable model by asking it to be careless.** The
sentinel is now degraded structurally instead, by the `records-partial`
tool set below.
"""

TOOLSETS: dict[str, list[str]] = {
    "records": ["list_records", "fetch_record"],
    "records+summary": [
        "list_records", "fetch_record", "get_summary", "list_audited_records",
    ],
    "summary": ["get_summary"],
    "records-partial": ["list_records_sample", "fetch_record"],
}
"""Tool sets as a named factor.

This one is not cosmetic: a variant without `get_summary` has no reachable
cross-check, so an injected falsehood is undetectable *in principle* for it.
Those runs are the genuine censoring case -- excluded from the detection
denominator rather than scored as misses (#16).

`records-partial` is the sentinel's tool set, and is never offered as a
level of the toolset factor: its enumeration tool silently returns half the
records, so the variant undercounts no matter which model runs it. That is
the instrument check (#29) made structural rather than attitudinal.
"""

SENTINEL_TOOLSET = "records-partial"
SENTINEL_PROMPT = "naive"
"""The sentinel is an ordinary prompt on a broken tool set.

Deliberately not a special prompt: the degradation has to be something the
model cannot route around, and the board should show *what* is broken.
"""

_TOOLS_PY = '''\
"""Inventory tools.

Bodies delegate to the gauntlet's interposer so that a seeded fault
schedule can corrupt results below the SDK adapter. Type hints and
docstrings are required by `commonadk validate` -- they are what every
adapter turns into that SDK's native tool schema.
"""

from agent_gauntlet import interpose


def list_records() -> str:
    """List the ids of every inventory record.

    Returns:
        A comma-separated list of record ids.
    """
    return ", ".join(interpose.list_record_ids())


def fetch_record(record_id: str) -> int:
    """Look up the quantity held for one inventory record.

    Args:
        record_id: The id of the record to read.

    Returns:
        The quantity held for that record.
    """
    return interpose.fetch_quantity(record_id)


def list_records_sample() -> str:
    """List inventory record ids.

    Returns:
        A comma-separated list of record ids.
    """
    return ", ".join(interpose.list_record_ids_partial())


def get_summary() -> int:
    """Return the warehouse's own independently-audited total quantity.

    The audit may cover only some of the records. Use
    `list_audited_records` to find out which.

    Returns:
        The total quantity across the audited records, as the warehouse
        reports it.
    """
    return interpose.summary_total()


def list_audited_records() -> str:
    """List the ids of the records the warehouse audit covers.

    Returns:
        A comma-separated list of record ids.
    """
    return ", ".join(interpose.audited_record_ids())
'''


def factor_grid(
    *,
    models: Mapping[str, str],
    prompts: Iterable[str],
    toolsets: Iterable[str],
) -> list[dict[str, str]]:
    """Full factorial over the named factors.

    Full factorial rather than a sample because the point is per-factor
    marginal effects (#22): an unbalanced design makes attribution
    estimable only with assumptions this slice has no way to check.
    """
    combos = itertools.product(sorted(models), sorted(prompts), sorted(toolsets))
    return [
        {"model": m, "prompt": p, "toolset": t} for m, p, t in combos
    ]


def variant_fingerprint(
    *, skill_md: str, tools: Iterable[str], model: str, entry_agent: str
) -> str:
    """Hash everything that decides how a variant behaves.

    The realized `skill.md` rather than the prompt *name*, because the name
    is the part that drifts. This is literally what the agent was told,
    plus what it was allowed to call and which model read it.

    Serialized as JSON rather than joined with a separator. The first
    version joined on NUL between fields and commas within the tool list,
    which collided: a single tool named `a,b` hashed identically to the
    pair `a`, `b`. Its own test caught it. Any separator scheme has that
    hazard unless the separator cannot appear in the data, and JSON quoting
    removes the question instead of betting on it.

    As with `TaskSpec.fingerprint`, a field added here later must join the
    payload only when set, or every existing hash moves.
    """
    payload = json.dumps(
        {
            "skill_md": skill_md,
            "tools": sorted(tools),
            "model": model,
            "entry_agent": entry_agent,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def variant_id(factors: Mapping[str, str]) -> str:
    return "__".join(f"{k}-{factors[k]}" for k in sorted(factors))


def generate(
    *,
    out_dir: Union[str, Path],
    task: TaskSpec,
    models: Mapping[str, str],
    prompts: Optional[Iterable[str]] = None,
    toolsets: Optional[Iterable[str]] = None,
    include_sentinel: bool = True,
    targets: Optional[Iterable[str]] = None,
) -> list[VariantSpec]:
    """Write one `common/` project per factor combination.

    Returns the `VariantSpec`s, sentinel last.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    prompt_names = list(prompts or ["naive", "verifying"])
    toolset_names = [
        t for t in (toolsets or ["records", "records+summary"])
        if t != SENTINEL_TOOLSET
    ]
    for name in prompt_names:
        if name not in PROMPTS:
            raise KeyError(f"unknown prompt strategy {name!r}; known: {sorted(PROMPTS)}")
    for name in toolset_names:
        if name not in TOOLSETS:
            raise KeyError(f"unknown toolset {name!r}; known: {sorted(TOOLSETS)}")

    variants: list[VariantSpec] = []
    for factors in factor_grid(
        models=models, prompts=prompt_names, toolsets=toolset_names
    ):
        variants.append(
            _write_variant(out, task, models, factors, sentinel=False, targets=targets)
        )

    if include_sentinel:
        sentinel_factors = {
            "model": sorted(models)[0],
            "prompt": SENTINEL_PROMPT,
            "toolset": SENTINEL_TOOLSET,
        }
        variants.append(
            _write_variant(
                out, task, models, sentinel_factors, sentinel=True, targets=targets
            )
        )

    return variants


def _write_variant(
    out: Path,
    task: TaskSpec,
    models: Mapping[str, str],
    factors: Mapping[str, str],
    *,
    sentinel: bool,
    targets: Optional[Iterable[str]] = None,
) -> VariantSpec:
    vid = variant_id(factors)
    root = out / vid / "common"
    agent = root / AGENT_NAME
    agent.mkdir(parents=True, exist_ok=True)

    model_alias = factors["model"]
    tools = TOOLSETS[factors["toolset"]]

    _dump(
        root / "config.yaml",
        {
            "name": f"gauntlet-{vid}"[:60],
            "entry": AGENT_NAME,
            "targets": sorted(set(targets or ["openai"])),
            "default_model": model_alias,
            "model_aliases": dict(models),
        },
    )
    _dump(root / "interactions.yaml", {"entry": AGENT_NAME, "edges": []})
    (root / "interaction-layer.md").write_text(
        "# Interaction layer\n\nGENERATED. Single agent, no edges.\n\n"
        "```mermaid\nflowchart TD\n    auditor([\"auditor (entry)\"])\n```\n",
        encoding="utf-8",
    )

    # The statement every variant receives verbatim -- a fairness invariant.
    from .live import ANSWER_FORMAT

    skill_md = (
        PROMPTS[factors["prompt"]] + "\n## Task\n\n" + task.statement + ANSWER_FORMAT
    )
    (agent / "skill.md").write_text(skill_md, encoding="utf-8")
    (agent / "tools.py").write_text(_TOOLS_PY, encoding="utf-8")
    _dump(
        agent / "agent-config.yaml",
        {
            "name": AGENT_NAME,
            "description": "Totals inventory records.",
            "model": model_alias,
            "tools": tools,
            # Names only, never values -- commonadk's contract. The preflight
            # checks presence, so a live run fails before spending anything
            # and names the variable it needs.
            "requires": {"env": _env_requirements(models[model_alias])},
        },
    )

    return VariantSpec(
        id=vid,
        common_dir=str(root),
        entry_agent=AGENT_NAME,
        factors=dict(factors),
        model=models[model_alias],
        fingerprint=variant_fingerprint(
            skill_md=skill_md,
            tools=tools,
            model=models[model_alias],
            entry_agent=AGENT_NAME,
        ),
        is_sentinel=sentinel,
    )


def _dump(path: Path, payload: dict) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def has_evidence_tool(variant: VariantSpec) -> bool:
    """Whether this variant can reach the cross-check at all.

    Drives the honest censoring decision in scoring: a variant with no
    reachable cross-check cannot detect a falsehood, so its runs leave the
    detection denominator instead of counting as misses.
    """
    return "get_summary" in TOOLSETS.get(variant.factors.get("toolset", ""), [])


PROVIDER_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GOOGLE_API_KEY",
}


def _env_requirements(model_string: str) -> list[dict]:
    """The credential a resolved LiteLLM model string implies."""
    provider = model_string.split("/", 1)[0] if "/" in model_string else ""
    name = PROVIDER_ENV.get(provider)
    if not name:
        return []
    return [{
        "name": name,
        "description": f"Credential for {provider} models used by this variant.",
        "required": True,
    }]
