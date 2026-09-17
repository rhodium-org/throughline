# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""SR-0195 and SR-0199 — ratify refuses an item it cannot ratify before it renders
or asks anything, and a run holding such an item writes nothing.

An item whose status cannot move to the ratified status is one ratify cannot
ratify. The precondition check did not ask about the move, so the item was rendered,
the ratifier was asked to confirm, and only the write refused it. In a batch the
items before it had already been signed. Where a project declares that ratification
leaves the status where it is (SR-0172), no move is involved and nothing is refused.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.storage import load_project

DISALLOWED = "status change 'draft' -> 'ratified' is not an allowed transition"


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _bytes(root: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in sorted(root.rglob("*.yml"))}


@pytest.fixture
def graph(tmp_path) -> Path:
    """An intent, a machine-authored requirement born proposed (ratifiable), and a
    human-authored one born draft, a status the shipped transitions do not let move
    straight to ratified."""
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "Why",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "Proposed", "--text",
                 "The Tool shall do A.", "--origin", "ai", "--ground", "INT-0001",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "Drafted", "--text",
                 "The Tool shall do B.", "--ground", "INT-0001",
                 "--no-interactive"]) == 0
    project = load_project(root)
    assert project.get("REQ-0001").status == "proposed"
    assert project.get("REQ-0002").status == "draft"
    return root


def _tty_marking_prompts(monkeypatch, answers):
    """An interactive terminal whose every prompt leaves a marker in the stream the
    rendering also writes to; with no answers left, any prompt fails the test."""
    import throughline.cli as climod
    monkeypatch.setattr(climod, "_interactive", lambda: True)
    supply = iter(answers)

    def _input(*_a, **_k):
        print("<<asked>>", file=sys.stderr)
        return next(supply)

    monkeypatch.setattr("builtins.input", _input)


def test_a_disallowed_move_is_refused_before_rendering_or_asking(graph, monkeypatch,
                                                                 capsys):
    before = _bytes(graph)
    _tty_marking_prompts(monkeypatch, [])
    assert _cli(["-C", graph, "ratify", "REQ-0002", "--by", "Ada"]) == 2
    err = capsys.readouterr().err
    assert DISALLOWED in err
    assert "<<asked>>" not in err                     # nobody was asked
    assert "The Tool shall do B." not in err          # and nothing was rendered
    assert _bytes(graph) == before


def test_a_disallowed_move_is_refused_before_asking_who_is_signing(graph, monkeypatch,
                                                                  capsys):
    _tty_marking_prompts(monkeypatch, [])
    assert _cli(["-C", graph, "ratify", "REQ-0002"]) == 2
    err = capsys.readouterr().err
    assert DISALLOWED in err and "<<asked>>" not in err


@pytest.mark.parametrize("uids", [["REQ-0001", "REQ-0002"], ["REQ-0002", "REQ-0001"]])
def test_a_run_holding_one_such_item_writes_nothing(graph, capsys, uids):
    before = _bytes(graph)
    assert _cli(["-C", graph, "ratify", *uids, "--by", "Ada"]) == 2
    err = capsys.readouterr().err
    assert f"REQ-0002: {DISALLOWED}" in err
    assert "nothing in this run was ratified" in err
    assert _bytes(graph) == before


def test_a_run_holding_one_such_item_renders_and_asks_nothing(graph, monkeypatch,
                                                             capsys):
    before = _bytes(graph)
    _tty_marking_prompts(monkeypatch, [])
    assert _cli(["-C", graph, "ratify", "REQ-0001", "REQ-0002", "--by", "Ada"]) == 2
    err = capsys.readouterr().err
    assert "<<asked>>" not in err and "The Tool shall do A." not in err
    assert _bytes(graph) == before


def test_the_ratifiable_item_is_still_ratified_on_its_own(graph):
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    item = load_project(graph).get("REQ-0001")
    assert item.status == "ratified" and item.attrs["ratified_by"] == "Ada"


def test_nothing_is_refused_where_ratification_leaves_the_status_alone(graph):
    """SR-0172: a project that declares ratification does not move the status signs
    an item wherever it stands, so no transition can be in the way."""
    config = graph / "throughline.toml"
    text = config.read_text(encoding="utf-8")
    assert "[ratify]" not in text
    config.write_text(text + "\n[ratify]\nmoves_status = false\n", encoding="utf-8")
    assert _cli(["-C", graph, "ratify", "REQ-0002", "--by", "Ada"]) == 0
    item = load_project(graph).get("REQ-0002")
    assert item.status == "draft" and item.attrs["ratified_by"] == "Ada"


def test_the_refusal_is_the_one_the_write_would_raise(graph):
    """The early refusal and the write are one predicate (SR-0195), so the library
    call refuses in the same words the command prints."""
    from throughline.graph import Index
    from throughline.grounding import GroundingError, ratification_obstacle, ratify
    project = load_project(graph)
    item = project.get("REQ-0002")
    obstacle = ratification_obstacle(project.schema, Index.build(project), item)
    assert obstacle == f"REQ-0002: {DISALLOWED}"
    with pytest.raises(GroundingError) as exc:
        ratify(project, "REQ-0002", by="Ada")
    assert str(exc.value) == obstacle
