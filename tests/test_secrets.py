"""Credentials: supplying a value without ever writing one down.

The project rule was never "the operator may not supply a key" -- it is
that a *spec* must not carry one. What gets written down and shared is the
NAME: the project file, the ledger, a generated `common/` folder, a
screenshot of the board. Without any way to supply a value, the live path
simply could not be exercised from the UI, which is not a security property,
just a missing feature.

So these pin the boundary that actually matters: a value can go in, and
there is no path by which one comes back out.

The secret used throughout is a fixed sentinel, and every assertion is the
same question asked of a different surface -- is that string anywhere it
could be read, logged, committed or screenshotted?
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from agent_gauntlet import project as projects, secrets, server

SENTINEL = "sk-ant-THIS-MUST-NEVER-APPEAR-anywhere"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAUNTLET_HOME", str(tmp_path / "store"))
    # Both, and not just the one under test: this environment really does
    # carry an OPENAI_API_KEY, and a presence assertion that passes because
    # of the developer's own shell is not an assertion.
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GAUNTLET_ENV_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(server, "SECRETS", secrets.Store())
    monkeypatch.setattr(server, "BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(server, "CODE_EXECUTION_ASSERTED", True)
    return tmp_path


# --- .env parsing ---------------------------------------------------------


def test_a_dotenv_is_data_and_never_a_program():
    """No shell expansion, no substitution, no interpolation. A credentials
    file treated as a program is how a config file becomes an execution
    path."""
    parsed = secrets.parse_env(
        "# comment\n"
        "export ANTHROPIC_API_KEY=\"sk-quoted\"\n"
        "OPENAI_API_KEY = sk-spaced \n"
        "HOME_LIKE=$HOME/nope\n"
        "SUBST=$(whoami)\n"
        "lowercase=ignored\n"
        "NO_EQUALS\n"
    )
    assert parsed["ANTHROPIC_API_KEY"] == "sk-quoted"
    assert parsed["OPENAI_API_KEY"] == "sk-spaced"
    assert parsed["HOME_LIKE"] == "$HOME/nope"      # literal, not expanded
    assert parsed["SUBST"] == "$(whoami)"           # literal, not executed
    assert "lowercase" not in parsed and "NO_EQUALS" not in parsed


def test_a_dotenv_does_not_shadow_the_shell(home, monkeypatch, tmp_path):
    """A value exported in the shell that started the server is the more
    deliberate of the two. Silently shadowing it would make 'which key did
    that run use?' unanswerable."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-the-shell")
    env = tmp_path / ".env"
    env.write_text(f"ANTHROPIC_API_KEY={SENTINEL}\nOPENAI_API_KEY=from-file\n")

    applied = secrets.load_env_file(env)
    assert applied == ["OPENAI_API_KEY"]
    assert os.environ["ANTHROPIC_API_KEY"] == "from-the-shell"

    # ...unless asked, which is what the reload button does.
    secrets.load_env_file(env, override=True)
    assert os.environ["ANTHROPIC_API_KEY"] == SENTINEL


def test_a_missing_or_unreadable_dotenv_is_not_an_error(tmp_path):
    assert secrets.load_env_file(tmp_path / "nope.env") == []


# --- names ----------------------------------------------------------------


@pytest.mark.parametrize("bad", [
    "lower_case", "WITH-DASH", "A", "", "HAS SPACE",
    "NAME\nOTHER=injected",          # would forge a second assignment
    "NAME=OTHER",
])
def test_a_name_that_could_forge_a_second_assignment_is_refused(bad):
    """The name is written into `.env` and read back as trusted config."""
    with pytest.raises(secrets.BadSecretName):
        secrets.check_name(bad)


def test_a_value_containing_a_newline_cannot_be_stored(tmp_path):
    with pytest.raises(ValueError, match="newline"):
        secrets.write_env_file(tmp_path / ".env", "ANTHROPIC_API_KEY",
                               "sk-a\nOPENAI_API_KEY=sk-forged")


# --- the store holds no values -------------------------------------------


def test_the_store_keeps_names_and_provenance_but_no_values(home):
    store = secrets.Store()
    store.set("ANTHROPIC_API_KEY", SENTINEL)

    assert os.environ["ANTHROPIC_API_KEY"] == SENTINEL
    # Everything the object itself carries, serialized as far as it goes.
    assert SENTINEL not in repr(store)
    assert SENTINEL not in json.dumps(store.sources)
    assert SENTINEL not in json.dumps(store.status(["ANTHROPIC_API_KEY"]))

    status = store.status(["ANTHROPIC_API_KEY", "OPENAI_API_KEY"])
    assert status[0] == {"name": "ANTHROPIC_API_KEY", "present": True,
                         "source": "session"}
    assert status[1] == {"name": "OPENAI_API_KEY", "present": False,
                         "source": None}


def test_forgetting_a_credential_removes_it_from_the_environment(home):
    store = secrets.Store()
    store.set("ANTHROPIC_API_KEY", SENTINEL)
    store.clear("ANTHROPIC_API_KEY")
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert store.status(["ANTHROPIC_API_KEY"])[0]["present"] is False


def test_a_variable_set_outside_this_server_is_labelled_as_such(home, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-the-shell")
    assert secrets.Store().status(["ANTHROPIC_API_KEY"])[0]["source"] == "environment"


# --- the value does not come back out ------------------------------------


def test_no_response_from_the_server_carries_a_value(home):
    """The whole point. Asked of every payload the server can produce about
    a project that needs a credential."""
    server.SECRETS.set("ANTHROPIC_API_KEY", SENTINEL)

    p = projects.new("live")
    p.statement = "Total it."
    p.offline = False
    p.budget_usd = 1.0
    p.models = [projects.ModelChoice(alias="cheap",
                                     model="anthropic/claude-haiku-4-5",
                                     credential="ANTHROPIC_API_KEY")]
    p.save()

    payloads = [
        json.dumps(server.project_view(p)),
        json.dumps(server.SECRETS.status(server.credential_names())),
        json.dumps(server.catalog()),
        json.dumps(projects.listing()),
        (p.dir / "project.json").read_text(encoding="utf-8"),
    ]
    for payload in payloads:
        assert SENTINEL not in payload
    # ...and the name is still there, because that is what gets checked.
    assert "ANTHROPIC_API_KEY" in payloads[0]


def test_nothing_is_written_to_disk_unless_asked(home):
    server.SECRETS.set("ANTHROPIC_API_KEY", SENTINEL)
    store_env = projects.root() / ".env"
    assert not store_env.exists()

    for path in projects.root().rglob("*"):
        if path.is_file():
            assert SENTINEL not in path.read_text(encoding="utf-8", errors="ignore")


def test_persisting_writes_one_line_owner_only(home):
    target = projects.root() / ".env"
    secrets.write_env_file(target, "ANTHROPIC_API_KEY", SENTINEL)
    assert target.read_text().strip() == f"ANTHROPIC_API_KEY={SENTINEL}"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600

    # Replaced, not appended twice -- two lines for one name is a file where
    # which value wins depends on parse order.
    secrets.write_env_file(target, "ANTHROPIC_API_KEY", "sk-second")
    lines = [ln for ln in target.read_text().splitlines() if ln.strip()]
    assert lines == ["ANTHROPIC_API_KEY=sk-second"]


def test_the_project_store_ignores_itself_in_git(home):
    """`GAUNTLET_HOME` can point anywhere, including inside a checkout, and
    the store holds uploaded source and possibly a .env."""
    projects.ensure_root()
    ignore = projects.root() / ".gitignore"
    assert ignore.is_file()
    assert ignore.read_text().strip().endswith("*")


# --- the loopback rule ----------------------------------------------------


def test_credentials_are_refused_over_a_network_bind(home, monkeypatch):
    """Accepting a credential from a non-loopback bind means accepting one
    from whoever can reach the port.

    The bind is necessary and not sufficient: the operator must also have
    asserted that nobody else can reach the port, which `home` does here
    and `test_reachability.py` pins on its own.
    """
    monkeypatch.setattr(server, "BIND_HOST", "0.0.0.0")
    assert not server.uploads_allowed()
    monkeypatch.setattr(server, "BIND_HOST", "127.0.0.1")
    assert server.uploads_allowed()


def test_the_error_for_a_bad_name_never_quotes_the_value():
    """An error string is the easiest thing in a system to end up in a log."""
    store = secrets.Store()
    with pytest.raises(secrets.BadSecretName) as exc:
        store.set("bad name", SENTINEL)
    assert SENTINEL not in str(exc.value)

    with pytest.raises(ValueError) as exc:
        store.set("ANTHROPIC_API_KEY", "")
    assert "ANTHROPIC_API_KEY" in str(exc.value)


# --- it actually unblocks a live run --------------------------------------


def test_setting_the_credential_clears_the_blocker(home):
    p = projects.new("live")
    p.statement = "Total it."
    p.offline = False
    p.budget_usd = 1.0
    p.models = [projects.ModelChoice(alias="cheap",
                                     model="anthropic/claude-haiku-4-5",
                                     credential="ANTHROPIC_API_KEY")]
    assert any("ANTHROPIC_API_KEY" in b for b in p.blockers())

    server.SECRETS.set("ANTHROPIC_API_KEY", SENTINEL)
    assert not any("ANTHROPIC_API_KEY" in b for b in p.blockers())


def test_each_model_declares_its_own_providers_credential():
    """The page used to hardcode one provider's variable, so a grid that
    grew a second provider would have checked the wrong key."""
    creds = server.catalog()["model_credentials"]
    assert creds
    for alias, model in server.DEFAULT_MODELS.items():
        expected = model.split("/", 1)[0].upper() + "_API_KEY"
        assert creds[alias] == expected
