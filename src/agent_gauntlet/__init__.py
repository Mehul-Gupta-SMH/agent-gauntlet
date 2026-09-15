"""agent-gauntlet -- search agent configurations under adversarial fault injection.

Design stage. See plan.md and the issue tracker; M0 (the measurement gate)
is the only milestone with code behind it.
"""

from .analyze import DetectionReport, StabilityReport, kendall_tau, stability
from .faults import FaultKind, FaultSchedule, InjectedFault
from .interpose import RunContext, ToolTimeout, run_context
from .ledger import Ledger, RunRecord
from .matrix import run_matrix, seed_for
from .score import Answer, Outcome, Score, score_run
from .spec import Oracle, Scenario, TaskSpec, VariantSpec

__all__ = [
    "Answer", "DetectionReport", "FaultKind", "FaultSchedule", "InjectedFault",
    "Ledger", "Oracle", "Outcome", "RunContext", "RunRecord", "Scenario",
    "Score", "StabilityReport", "TaskSpec", "ToolTimeout", "VariantSpec",
    "kendall_tau", "run_context", "run_matrix", "score_run", "seed_for",
    "stability",
]
