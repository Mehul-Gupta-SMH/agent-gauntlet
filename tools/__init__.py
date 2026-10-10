"""Helper scripts the workflows call.

A package only so `tests/` can import these rather than shelling out to
them; the wheel ships `src/agent_gauntlet` alone (see `pyproject.toml`), so
nothing here is installed. These are repository tooling, not library code,
and the tests that cover them exist because a workflow is otherwise the one
thing in this project with no test at all.
"""
