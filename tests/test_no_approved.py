"""The lifecycle `tl init` writes has no status that claims an agreement no
signature records (SR-0305); only `tl ratify` puts an item in the ratified status
(SR-0306); a graph that already declares `approved` keeps it (SR-0307); and the
brief never tells an agent to set it (SR-0088)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from throughline.cli import main as _cli
from throughline.storage import load_project


def _init(tmp_path: Path, *extra: str) -> Path:
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--name", "t", *extra]) == 0
    return root


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True)


def _commit(root: Path) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "step")


# --------------------------------------------------------------------- SR-0305

def test_the_shipped_lifecycle_declares_no_approved_status(tmp_path):
    schema = load_project(_init(tmp_path)).schema
    assert "approved" not in schema.statuses
    assert "approved" not in schema.transitions
    assert not any("approved" in moves for moves in schema.transitions.values())


def test_the_only_status_that_reads_agreed_is_the_ratified_role(tmp_path):
    """Every status an item can move on to from the birth statuses without
    `tl ratify` is one that says nothing is agreed: the initial, proposed and
    parked statuses, and the ones an item is withdrawn into. (Out of suspect an
    item returns to where it stood, which is not a move forward.)"""
    schema = load_project(_init(tmp_path)).schema
    roles = schema.status_roles
    ratified = roles["ratified"]
    seen, queue = set(schema.birth_statuses()), list(schema.birth_statuses())
    while queue:
        here = queue.pop()
        for nxt in schema.transitions.get(here, ()):
            if nxt != ratified and here != roles["suspect"] and nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    assert seen == {roles["initial"], roles["proposed"], "deferred",
                    roles["suspect"], roles["invalidated"], roles["tombstone"]}


def test_draft_and_deferred_reach_ratified_through_proposed(tmp_path):
    schema = load_project(_init(tmp_path)).schema
    proposed, ratified = (schema.status_role("proposed"),
                          schema.status_role("ratified"))
    for parked in (schema.status_role("initial"), "deferred"):
        assert proposed in schema.transitions[parked]
        assert ratified not in schema.transitions[parked]
    assert ratified in schema.transitions[proposed]


def test_the_starter_graph_is_seeded_in_the_initial_status(tmp_path):
    root = _init(tmp_path)
    project = load_project(root)
    initial = project.schema.status_role("initial")
    assert {item.status for item in project.items()} == {initial}
    assert _cli(["-C", str(root), "check", "--strict"]) == 0


# --------------------------------------------------------------------- SR-0306

def test_tl_new_refuses_the_ratified_status_and_writes_nothing(tmp_path, capsys):
    root = _init(tmp_path)
    before = sorted(p.name for p in (root / "requirements").iterdir())
    assert _cli(["-C", str(root), "new", "REQ", "--title", "signed by nobody",
                 "--status", "ratified", "--ground", "INT-0001",
                 "--no-interactive"]) == 2
    assert "tl ratify" in capsys.readouterr().err
    assert sorted(p.name for p in (root / "requirements").iterdir()) == before


def test_tl_status_refuses_the_ratified_status(tmp_path, capsys):
    root = _init(tmp_path)
    assert _cli(["-C", str(root), "status", "REQ-0001", "proposed"]) == 0
    capsys.readouterr()
    assert _cli(["-C", str(root), "status", "REQ-0001", "ratified"]) == 2
    assert "tl ratify" in capsys.readouterr().err
    assert load_project(root).get("REQ-0001").status == "proposed"


def test_the_refusal_follows_the_role_not_the_word(tmp_path):
    """A project that binds the ratified role to its own word is refused that word,
    and may still name a status called 'ratified' that plays no role."""
    root = _init(tmp_path, "--bare")
    cfg = root / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8")
                   .replace('ratified = "ratified"', 'ratified = "accepted"')
                   .replace('values = ["proposed",', 'values = ["accepted", "proposed",'),
                   encoding="utf-8")
    assert _cli(["-C", str(root), "register", "new", "INT", "vision"]) == 0
    assert _cli(["-C", str(root), "new", "INT", "--type", "intent", "--title", "why",
                 "--status", "accepted"]) == 2
    assert _cli(["-C", str(root), "new", "INT", "--type", "intent", "--title", "why",
                 "--status", "ratified"]) == 0


def test_tl_ratify_still_moves_a_proposed_item_to_ratified(tmp_path):
    root = _init(tmp_path)
    assert _cli(["-C", str(root), "status", "REQ-0001", "proposed"]) == 0
    assert _cli(["-C", str(root), "ratify", "REQ-0001", "--by", "Ada Lovelace"]) == 0
    item = load_project(root).get("REQ-0001")
    assert item.status == "ratified"
    assert item.ratifications[-1]["by"] == "Ada Lovelace"


# --------------------------------------------------------------------- SR-0307

def _with_approved(root: Path) -> None:
    """The configuration `tl init` wrote before SR-0305."""
    cfg = root / "throughline.toml"
    text = cfg.read_text(encoding="utf-8")
    text = text.replace('values = ["proposed", "draft", "deferred", "ratified",',
                        'values = ["proposed", "draft", "deferred", "approved", "ratified",')
    text = text.replace('draft       = ["proposed", ',
                        'draft       = ["proposed", "approved", ')
    text = text.replace('[transitions]\n',
                        '[transitions]\napproved    = ["ratified", "implemented", '
                        '"deferred", "suspect", "rejected", "deleted"]\n')
    cfg.write_text(text, encoding="utf-8")


def test_a_graph_that_declares_approved_keeps_it(tmp_path):
    root = _init(tmp_path)
    _with_approved(root)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    _commit(root)
    assert _cli(["-C", str(root), "status", "REQ-0001", "approved"]) == 0
    _commit(root)
    assert _cli(["-C", str(root), "check", "--strict"]) == 0

    config = (root / "throughline.toml").read_text(encoding="utf-8")
    assert _cli(["-C", str(root), "migrate"]) == 0
    assert (root / "throughline.toml").read_text(encoding="utf-8") == config
    project = load_project(root)
    assert "approved" in project.schema.statuses
    assert project.get("REQ-0001").status == "approved"


# --------------------------------------------------------------------- SR-0088

def test_the_brief_never_tells_an_agent_to_set_approved(tmp_path, capsys):
    """Neither for a new project nor for one that still declares the status."""
    root = _init(tmp_path)
    for declared in (False, True):
        if declared:
            _with_approved(root)
        capsys.readouterr()
        assert _cli(["-C", str(root), "context"]) == 0
        brief = capsys.readouterr().out
        contract = brief.split("## How to work here")[0]
        assert "approved" not in contract
        assert "Implement it once a human has ratified it (`ratified`), then " \
               "move it to `implemented`" in contract
