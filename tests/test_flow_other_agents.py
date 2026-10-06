"""Other agents: herdr agents that are not Firstmate panes, on the All tab."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import re
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
FM_CHILD_LABEL = "└ fresh-task · p:" + "A" * 22


def _agent(pane: str, status: str, *, ws: str = "", tab: str = "", cwd: str = "/Users/me",
           agent: str = "claude", title: str = "") -> dict:
    ws = ws or pane.split(":", 1)[0]
    return {
        "pane_id": pane,
        "workspace_id": ws,
        "tab_id": tab or f"{ws}:t1",
        "agent": agent,
        "agent_status": status,
        "cwd": cwd,
        "foreground_cwd": cwd,
        "terminal_title_stripped": title,
    }


def _state(agents: list[dict], workspaces: dict[str, str] | None = None,
           tabs: dict[str, str] | None = None) -> dict:
    return {
        "agents": {a["pane_id"]: a for a in agents},
        "panes": {a["pane_id"]: a for a in agents},
        "workspaces": {k: {"workspace_id": k, "label": v} for k, v in (workspaces or {}).items()},
        "tabs": {k: {"tab_id": k, "label": v} for k, v in (tabs or {}).items()},
    }


def _snapshot_json(agents: list[dict]) -> str:
    return json.dumps({"id": "x", "result": {"snapshot": {
        "agents": agents,
        "panes": agents,
        "workspaces": [{"workspace_id": "wA", "label": "alpha"}],
        "tabs": [{"tab_id": "wA:t1", "label": "1"}],
    }}})


@contextlib.contextmanager
def _patched(name: str, value):
    old = getattr(flow, name)
    setattr(flow, name, value)
    try:
        yield
    finally:
        setattr(flow, name, old)


# ---- herdr state: one `herdr api snapshot` call per tick ----------------------


def test_herdr_state_reads_one_snapshot():
    calls: list[list[str]] = []

    def fake_run(cmd, timeout=25.0):
        calls.append(cmd[1:])
        return _snapshot_json([_agent("wA:p1", "idle", ws="wA")])

    with _patched("_run", fake_run):
        state = flow.herdr_state()
    assert calls == [["api", "snapshot"]]
    assert list(state["agents"]) == ["wA:p1"]
    assert list(state["panes"]) == ["wA:p1"]
    assert state["workspaces"]["wA"]["label"] == "alpha"
    assert state["tabs"]["wA:t1"]["label"] == "1"


def test_herdr_state_falls_back_to_the_two_lists():
    agent = _agent("wA:p1", "working", ws="wA")

    def fake_run(cmd, timeout=25.0):
        if cmd[1:] == ["api", "snapshot"]:
            return None
        if cmd[1:] == ["agent", "list"]:
            return json.dumps({"result": {"agents": [agent]}})
        if cmd[1:] == ["pane", "list"]:
            return json.dumps({"result": {"panes": [agent]}})
        raise AssertionError(cmd)

    with _patched("_run", fake_run):
        state = flow.herdr_state()
    assert list(state["agents"]) == ["wA:p1"]
    assert list(state["panes"]) == ["wA:p1"]
    assert state["workspaces"] == {} and state["tabs"] == {}


def test_herdr_agents_keeps_its_two_dict_shape():
    state = _state([_agent("wA:p1", "idle", ws="wA")])
    with _patched("herdr_state", lambda: state):
        agents, panes = flow.herdr_agents()
    assert list(agents) == ["wA:p1"] and list(panes) == ["wA:p1"]


# ---- which agents are Firstmate's ---------------------------------------------


def test_firstmate_panes_are_left_out():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "firstmate"
        (home / "state").mkdir(parents=True)
        meta = home / "state" / "crew-task.meta"
        meta.write_text("harness=claude\nherdr_pane_id=w2:p1\n")
        homes = [Home("captain", str(home), "captain")]
        meta_index = {"crew-task": (str(meta), str(home))}
        state = _state(
            [
                _agent("w1:p1", "done", cwd=str(home)),  # the supervisor, by home cwd
                _agent("w2:p1", "working"),  # a crew pane, by its task meta
                _agent("w3:p1", "working", ws="w3"),  # a fresh crew, by its workspace label
                _agent("w4:p1", "idle"),
                _agent("w5:p2", "blocked"),
            ],
            workspaces={"w3": FM_CHILD_LABEL, "w4": "beta", "w5": "alpha"},
        )
        fm_panes = flow.firstmate_pane_ids(meta_index)
        cards = flow.other_agent_cards(state, homes, fm_panes, {}, 100.0)
    assert fm_panes == {"w2:p1"}
    assert sorted(c.pane_id for c in cards) == ["w4:p1", "w5:p2"]


def test_other_agents_put_blocked_first():
    statuses = {"w1:p1": "idle", "w2:p1": "working", "w3:p1": "blocked", "w4:p1": "done"}
    state = _state([_agent(p, s) for p, s in statuses.items()])
    cards = flow.other_agent_cards(state, [], set(), {}, 100.0)
    assert [c.live_status for c in cards] == ["blocked", "done", "working", "idle"]


def test_other_agent_card_names_its_workspace_tab_and_title():
    state = _state(
        [_agent("w7:p3", "blocked", ws="w7", tab="w7:t3", title="Refactor the parser")],
        workspaces={"w7": "alpha"},
        tabs={"w7:t3": "3"},
    )
    (card,) = flow.other_agent_cards(state, [], set(), {}, 100.0)
    assert card.bucket == "agents"
    assert card.id == "alpha"
    assert card.task == "w7:p3"
    assert "blocked" in card.badge
    assert card.agent == "claude"
    assert card.title == "Refactor the parser"
    assert card.mode == "tab 3 · w7:p3"
    assert (card.pane_id, card.tab_id, card.workspace_id) == ("w7:p3", "w7:t3", "w7")


def test_a_named_tab_keeps_its_name_in_the_footer():
    state = _state([_agent("w8:p6", "idle", ws="w8", tab="w8:t6")], tabs={"w8:t6": "review"})
    (card,) = flow.other_agent_cards(state, [], set(), {}, 100.0)
    assert card.mode == "review · w8:p6"


# ---- time in the current state -----------------------------------------------


def test_track_agent_states_records_each_change():
    since = flow.track_agent_states({}, {"w1:p1": {"agent_status": "idle"}}, 10.0)
    assert since == {"w1:p1": ("idle", 10.0, False)}
    since = flow.track_agent_states(since, {"w1:p1": {"agent_status": "idle"}}, 20.0)
    assert since == {"w1:p1": ("idle", 10.0, False)}
    since = flow.track_agent_states(since, {"w1:p1": {"agent_status": "blocked"}}, 30.0)
    assert since == {"w1:p1": ("blocked", 30.0, True)}
    assert flow.track_agent_states(since, {}, 40.0) == {}


def test_card_shows_the_time_in_its_state():
    state = _state([_agent("w1:p1", "blocked"), _agent("w2:p1", "idle")])
    since = {"w1:p1": ("blocked", 750.0, True), "w2:p1": ("idle", 750.0, False)}
    cards = {c.pane_id: c for c in flow.other_agent_cards(state, [], set(), since, 1000.0)}
    assert cards["w1:p1"].doing == "for 4m 10s"
    # a state already present when the deck started is at least that old
    assert cards["w2:p1"].doing == "for ≥4m 10s"


# ---- collector and board ------------------------------------------------------


def _all_home() -> Home:
    return Home(flow.ALL_CREW_LABEL, "", "all")


def test_collector_publishes_other_agents_on_the_all_tab_only():
    state = _state([_agent("w4:p1", "idle"), _agent("w5:p2", "blocked")])
    collector = flow.Collector()
    crew = Home("captain", "/nowhere", "captain")
    homes = [_all_home(), crew]
    with _patched("herdr_state", lambda: state):
        agents, panes = collector._refresh_agents(homes, 100.0)
    assert set(agents) == {"w4:p1", "w5:p2"}
    empty = {key: [] for key, _ in flow.COLUMNS}
    fleet = collector._snapshot(homes, homes[0], dict(empty), agents, panes)
    mate = collector._snapshot(homes, crew, dict(empty), agents, panes)
    assert [c.pane_id for c in fleet.cols["agents"]] == ["w5:p2", "w4:p1"]
    assert "agents" not in mate.cols
    assert [c.pane_id for c in mate.others] == ["w5:p2", "w4:p1"]


def _render(snap) -> list[str]:
    ui = flow.UI()
    ui.show_landed = True  # independent of the machine's show_landed config
    frame = io.StringIO()
    with contextlib.redirect_stdout(frame):
        ui.render(snap)
    return [ANSI.sub("", line) for line in ui.last_frame or []]


def _snap(home: Home, others: list) -> object:
    snap = flow.Snapshot()
    snap.homes = [_all_home(), Home("captain", "/fm", "captain")]
    snap.home = home
    snap.cols = {key: [] for key, _ in flow.COLUMNS}
    snap.others = others
    if flow.is_aggregate_home(home):
        snap.cols["agents"] = list(others)
    return snap


def test_all_tab_shows_an_other_agents_column_in_place_of_landed():
    state = _state([_agent("w4:p1", "idle"), _agent("w5:p2", "blocked")])
    others = flow.other_agent_cards(state, [], set(), {}, 100.0)
    lines = _render(_snap(_all_home(), others))
    assert "Other Agents (2)" in lines[2]
    assert "Landed" not in lines[2]


def test_a_crew_tab_keeps_its_five_columns():
    lines = _render(_snap(Home("captain", "/fm", "captain"), []))
    assert "Other Agents" not in lines[2]
    assert "Landed" in lines[2]


def test_header_counts_other_agents_and_blocked_ones_on_every_tab():
    state = _state([_agent("w4:p1", "idle"), _agent("w5:p2", "blocked")])
    others = flow.other_agent_cards(state, [], set(), {}, 100.0)
    header = _render(_snap(Home("captain", "/fm", "captain"), others))[0]
    assert "2 other agents" in header
    assert "⛔ 1 blocked" in header


def test_once_lists_the_other_agents():
    state = _state([_agent("w5:p2", "blocked", ws="w5")], workspaces={"w5": "alpha"})
    home = Home("captain", "/fm", "captain")
    out = io.StringIO()
    with _patched("discover_homes", lambda: [_all_home(), home]), \
            _patched("herdr_state", lambda: state), \
            _patched("bearings_snapshot", lambda h: {}), \
            contextlib.redirect_stdout(out):
        assert flow.once() == 0
    text = out.getvalue()
    assert "== Other agents (1)" in text
    assert "w5:p2 [⛔ blocked] claude alpha" in text


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ok")
