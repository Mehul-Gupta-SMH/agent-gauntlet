"""Who can reach this port, and why the server stopped guessing.

The upload guard used to ask one question -- *what interface did I bind?*
-- and treat the answer as though it were a different question: *can a
stranger reach me?* Those come apart in the single most common way anyone
shows a demo. `ngrok http 8420`, `cloudflared tunnel --url
http://localhost:8420`, a forwarded port in an editor, `ssh -R`: every one
of them forwards to loopback. The bind stayed `127.0.0.1`, the guard stayed
open, and whoever had the URL could upload a Python file that the server
imports and calls in its own process, with whatever provider keys are in
its environment.

So reachability is asserted now, not inferred. The operator passes
`--allow-code-execution`; the bind check survives as a second condition
rather than the only one; and each request is additionally refused if it
carries a forwarding header, because a tunnel can be attached to a server
that is already running and nothing about the bind will change when it is.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from agent_gauntlet import project as projects, secrets, server


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAUNTLET_HOME", str(tmp_path / "projects"))
    monkeypatch.delenv("GAUNTLET_ENV_FILE", raising=False)
    monkeypatch.setattr(server, "SECRETS", secrets.Store())
    return tmp_path


@pytest.fixture
def live(home, monkeypatch):
    """A real server on an ephemeral port, asserted and bound to loopback.

    The per-request half of the guard cannot be tested by calling a
    function: it exists because headers arrive after startup. So this runs
    the actual handler over an actual socket.
    """
    monkeypatch.setattr(server, "BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(server, "CODE_EXECUTION_ASSERTED", True)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def post(base, path, body, headers=None):
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(), method="POST",
        headers={"content-type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as res:
        return res.status, json.loads(res.read())


# --- the regression itself ------------------------------------------------


def test_a_loopback_bind_is_not_a_statement_about_who_can_reach_you(monkeypatch):
    """The hole, pinned. This configuration -- bound to loopback, no flag --
    is exactly what a tunnelled demo looks like from inside the process, and
    it used to allow uploads."""
    monkeypatch.setattr(server, "BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(server, "CODE_EXECUTION_ASSERTED", False)
    assert not server.uploads_allowed()


def test_the_flag_does_not_survive_a_public_bind(monkeypatch):
    """The other direction. An operator who asserts reachability and then
    binds every interface has asserted something untrue, and the condition
    they did not remove still holds."""
    monkeypatch.setattr(server, "BIND_HOST", "0.0.0.0")
    monkeypatch.setattr(server, "CODE_EXECUTION_ASSERTED", True)
    assert not server.uploads_allowed()


@pytest.mark.parametrize("asserted,host,allowed", [
    (False, "127.0.0.1", False),
    (False, "0.0.0.0", False),
    (True, "127.0.0.1", True),
    (True, "0.0.0.0", False),
])
def test_both_conditions_are_required(monkeypatch, asserted, host, allowed):
    monkeypatch.setattr(server, "CODE_EXECUTION_ASSERTED", asserted)
    monkeypatch.setattr(server, "BIND_HOST", host)
    assert server.uploads_allowed() is allowed


def test_the_refusal_names_the_condition_that_actually_failed(monkeypatch):
    """An operator told the wrong reason goes and changes the wrong thing.
    The old message named the bind address even when the bind was fine."""
    monkeypatch.setattr(server, "BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(server, "CODE_EXECUTION_ASSERTED", False)
    assert "--allow-code-execution" in server.refusal_reason()

    monkeypatch.setattr(server, "BIND_HOST", "0.0.0.0")
    assert "0.0.0.0" in server.refusal_reason()
    assert "--allow-code-execution" not in server.refusal_reason()


def test_store_upload_refuses_without_the_assertion(home, monkeypatch):
    monkeypatch.setattr(server, "BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(server, "CODE_EXECUTION_ASSERTED", False)
    p = projects.new("no flag")
    with pytest.raises(PermissionError, match="allow-code-execution"):
        server.store_upload(p, "x.py", "def f():\n    return 1\n")
    assert not (p.tools_dir / "x.py").exists()


# --- the tunnel a running server cannot see -------------------------------


def test_a_forwarded_header_refuses_an_upload(live, home):
    """The flag is set and the bind is loopback -- every function-level
    check passes -- and the request still refuses, because it did not come
    from this machine."""
    p = projects.new("tunnelled")
    body = {"filename": "x.py", "source": "def f():\n    return 1\n"}

    status, data = post(live, f"/api/projects/{p.id}/upload", body,
                        {"X-Forwarded-For": "203.0.113.9"})
    assert status == 403
    assert "x-forwarded-for" in data["error"]
    assert not p.tools_dir.exists() or not list(p.tools_dir.glob("*.py"))

    # And the same request, direct, is not refused by this guard.
    status, data = post(live, f"/api/projects/{p.id}/upload", body)
    assert status != 403, data


def test_credentials_are_refused_through_a_tunnel(live, home):
    """A credential posted through a tunnel is a credential handed to
    whoever runs the tunnel."""
    status, data = post(live, "/api/secrets/set",
                        {"name": "ANTHROPIC_API_KEY", "value": "sk-not-real"},
                        {"CF-Connecting-IP": "203.0.113.9"})
    assert status == 403
    assert "cf-connecting-ip" in data["error"]
    # Refused before anything was stored, and the value never echoed back.
    assert "sk-not-real" not in json.dumps(data)
    assert server.SECRETS.sources == {}


@pytest.mark.parametrize("header", server.FORWARDED_HEADERS)
def test_every_forwarding_header_is_recognised(header):
    """Case is handled by the real header object, which the two live tests
    above exercise with `X-Forwarded-For` and `CF-Connecting-IP`."""
    assert server.looks_proxied({header: "x"}) == header
    assert server.looks_proxied({}) is None
    # Present but empty is not evidence of anything.
    assert server.looks_proxied({header: ""}) is None


def test_the_check_is_per_request_not_per_startup(live, home):
    """A tunnel can be attached to a server that is already up. The bind
    address does not change when it is, so a startup-time decision would
    never see it."""
    p = projects.new("later")
    body = {"filename": "x.py", "source": "def f():\n    return 1\n"}
    assert post(live, f"/api/projects/{p.id}/upload", body)[0] != 403
    assert post(live, f"/api/projects/{p.id}/upload", body,
                {"X-Real-IP": "203.0.113.9"})[0] == 403


def test_reading_the_board_still_works_through_a_proxy(live, home):
    """The refusal is scoped to the two routes that execute code or take a
    secret. Watching a run through a tunnel is the point of the UI."""
    status, _ = get(live, "/api/projects")
    assert status == 200


# --- what the operator and the page are told ------------------------------


def test_the_page_is_told_why_and_only_when_there_is_a_why(live, home, monkeypatch):
    _, data = get(live, "/api/projects")
    assert data["uploads_allowed"] is True
    assert "uploads_refusal" not in data

    monkeypatch.setattr(server, "CODE_EXECUTION_ASSERTED", False)
    _, data = get(live, "/api/projects")
    assert data["uploads_allowed"] is False
    assert "--allow-code-execution" in data["uploads_refusal"]


def test_the_flag_is_off_by_default_in_the_cli():
    from agent_gauntlet.cli import build_parser

    assert build_parser().parse_args(["ui"]).allow_code_execution is False
    assert build_parser().parse_args(
        ["ui", "--allow-code-execution"]).allow_code_execution is True


def test_the_cli_hands_the_assertion_to_the_server(monkeypatch):
    """The flag is worthless if it stops at the parser."""
    from agent_gauntlet import cli, server as srv

    seen = {}
    monkeypatch.setattr(srv, "serve", lambda **kw: seen.update(kw))
    cli.main(["ui", "--allow-code-execution"])
    assert seen == {"host": "127.0.0.1", "port": 8420,
                    "allow_code_execution": True}

    seen.clear()
    cli.main(["ui"])
    assert seen["allow_code_execution"] is False
