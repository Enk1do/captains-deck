"""Message Firstmate: the `m` key sends a typed message to the supervisor pane."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLOW = ROOT / "scripts" / "flow_tui.py"


def load_flow():
    spec = importlib.util.spec_from_file_location("flow_tui", FLOW)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["flow_tui"] = mod
    spec.loader.exec_module(mod)
    return mod


flow = load_flow()
Home = flow.Home
ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")

CAPTAIN = Home("captain", "/fm/captain", "captain")
MATE = Home("2ndmate-x", "/fm/mate", "secondmate")
ALL = Home(flow.ALL_CREW_LABEL, "", "all")


@contextlib.contextmanager
def _patched(name: str, value):
    old = getattr(flow, name)
    setattr(flow, name, value)
    try:
        yield
    finally:
        setattr(flow, name, old)


def _ui(active: Home, agents: dict | None = None):
    """A UI whose collector has published one snapshot for `active`."""
    snap = flow.Snapshot()
    snap.homes = [ALL, CAPTAIN, MATE]
    snap.home = active
    snap.agents = {
        "wC:p1": {"pane_id": "wC:p1", "agent": "claude", "cwd": CAPTAIN.path},
        "wM:p1": {"pane_id": "wM:p1", "agent": "claude", "cwd": "/elsewhere",
                  "foreground_cwd": MATE.path},
        "wO:p1": {"pane_id": "wO:p1", "agent": "claude", "cwd": "/elsewhere"},
    } if agents is None else agents
    ui = flow.UI()
    ui.collector._publish(snap)
    return ui


@contextlib.contextmanager
def _sender(reply=(True, "")):
    sent: list[tuple[str, str]] = []

    def fake(pane_id, text):
        sent.append((pane_id, text))
        return reply

    with _patched("send_to_firstmate", fake):
        yield sent


# ---- the box ------------------------------------------------------------------


def test_m_opens_the_box_and_esc_closes_it_without_a_send():
    ui = _ui(CAPTAIN)
    with _sender() as sent:
        flow.parse_input(b"m", ui)
        assert ui.message is not None
        flow.parse_input(b"\x1b", ui)
        assert ui.message is None
    assert sent == []


def test_the_box_owns_the_keys_while_open():
    ui = _ui(CAPTAIN)
    flow.parse_input(b"m", ui)
    flow.parse_input(b"quit now", ui)
    assert ui.message.text == "quit now"
    assert ui.quitting is False
    flow.parse_input(b"\x7f", ui)
    assert ui.message.text == "quit no"


# ---- sending ------------------------------------------------------------------


def test_enter_sends_once_to_the_supervisor_of_the_tab_on_screen():
    ui = _ui(MATE)
    with _sender() as sent:
        flow.parse_input(b"m", ui)
        flow.parse_input(b"start two tasks", ui)
        flow.parse_input(b"\r", ui)
    assert sent == [("wM:p1", "start two tasks")]
    assert ui.message is None
    assert ui.flash == "sent to firstmate (wM:p1)"


def test_the_all_tab_sends_to_the_captain_home_supervisor():
    ui = _ui(ALL)
    with _sender() as sent:
        flow.parse_input(b"m", ui)
        flow.parse_input(b"status?", ui)
        flow.parse_input(b"\r", ui)
    assert sent == [("wC:p1", "status?")]


def test_line_breaks_become_spaces():
    ui = _ui(CAPTAIN)
    with _sender() as sent:
        ui.open_message()
        ui.message.text = "first line\nsecond\r\nthird"
        ui.message_send()
    assert sent == [("wC:p1", "first line second third")]


# ---- refusals -----------------------------------------------------------------


def test_an_empty_message_is_refused():
    ui = _ui(CAPTAIN)
    with _sender() as sent:
        flow.parse_input(b"m", ui)
        flow.parse_input(b"\r", ui)
    assert sent == []
    assert ui.message is not None and "empty" in ui.message.error


def test_a_message_over_the_limit_is_refused():
    ui = _ui(CAPTAIN)
    with _sender() as sent:
        ui.open_message()
        ui.message.text = "x" * (flow.MESSAGE_LIMIT + 1)
        ui.message_send()
    assert sent == []
    assert str(flow.MESSAGE_LIMIT) in ui.message.error


def test_a_missing_supervisor_is_refused():
    ui = _ui(CAPTAIN, agents={})
    with _sender() as sent:
        flow.parse_input(b"m", ui)
        flow.parse_input(b"hello", ui)
        flow.parse_input(b"\r", ui)
    assert sent == []
    assert "no Firstmate pane" in ui.message.error


def test_a_blocked_reply_shows_the_refusal_and_keeps_the_text():
    ui = _ui(CAPTAIN)
    with _sender(reply=(False, "agent_blocked: agent is waiting at a dialog")) as sent:
        flow.parse_input(b"m", ui)
        flow.parse_input(b"hello", ui)
        flow.parse_input(b"\r", ui)
    assert sent == [("wC:p1", "hello")]
    assert ui.message is not None
    assert ui.message.text == "hello"
    assert "agent_blocked" in ui.message.error


# ---- the herdr call (a fake herdr; never the real one) --------------------------


def _fake_herdr(tmp: str, body: str) -> str:
    path = os.path.join(tmp, "herdr")
    with open(path, "w") as fh:
        fh.write("#!/bin/sh\n" + body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


def test_send_to_firstmate_runs_herdr_agent_prompt():
    with tempfile.TemporaryDirectory() as tmp:
        argv_file = os.path.join(tmp, "argv")
        script = _fake_herdr(tmp, f'for a in "$@"; do printf "%s\\n" "$a"; done > "{argv_file}"\n')
        with _patched("HERDR_BIN", script):
            ok, detail = flow.send_to_firstmate("wC:p1", "hi there")
        argv = Path(argv_file).read_text().splitlines()
    assert (ok, detail) == (True, "")
    assert argv == ["agent", "prompt", "wC:p1", "hi there"]


def test_send_to_firstmate_reports_the_herdr_error():
    err = json.dumps({"error": {"code": "agent_blocked", "message": "agent is waiting at a dialog"}})
    with tempfile.TemporaryDirectory() as tmp:
        script = _fake_herdr(tmp, f"echo '{err}' >&2\nexit 1\n")
        with _patched("HERDR_BIN", script):
            ok, detail = flow.send_to_firstmate("wC:p1", "hi")
    assert ok is False
    assert detail == "agent_blocked: agent is waiting at a dialog"


# ---- help, render, README -----------------------------------------------------


def test_help_lists_the_m_key():
    assert ("m", "message Firstmate") in flow.UI.HELP_ROWS


def test_render_shows_the_box_and_its_target():
    ui = _ui(CAPTAIN)
    flow.parse_input(b"m", ui)
    flow.parse_input(b"hello", ui)
    with contextlib.redirect_stdout(io.StringIO()):
        ui.render(ui.collector.snapshot())
    text = "\n".join(ANSI.sub("", line) for line in ui.last_frame or [])
    assert "Message Firstmate" in text
    assert "wC:p1" in text
    assert "hello" in text
    assert "enter send" in text


def test_readme_lists_the_m_key():
    readme = (ROOT / "README.md").read_text()
    assert "| `m` |" in readme


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ok")
