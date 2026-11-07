# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""SR-0195 and SR-0199 — ratify refuses an item it cannot ratify before it renders
or asks anything, and a run holding such an item writes nothing.

A draft is one ratify cannot ratify (SR-0307): it is agreed or deleted, never
signed. The precondition check once did not ask, so the item was rendered, the
ratifier was asked to confirm, and only the write refused it. In a batch the items
before it had already been signed. A draft is refused whether or not ratification
moves the status (SR-0172).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.storage import load_project

DISALLOWED = "REQ-0002 is a draft, and a draft is neither signed nor counted"


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _bytes(root: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in sorted(root.rglob("*.yml"))}


@pytest.fixture
def graph(tmp_path) -> Path:
    """An intent, a requirement born proposed (ratifiable), and one left in a draft
    status the project keeps apart from proposal (SR-0307), which the shipped
    lifecycle no longer has, so the project declares it."""
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
    config = root / "throughline.toml"
    text = config.read_text(encoding="utf-8")
    text = text.replace('values = ["proposed", "deferred",',
                        'values = ["proposed", "draft", "deferred",')
    text = text.replace('initial = "proposed"', 'initial = "draft"')
    text = text.replace('[transitions]\n', '[transitions]\ndraft = ["proposed", "deleted"]\n')
    config.write_text(text, encoding="utf-8")
    req = root / "requirements" / "REQ-0002.yml"
    if not req.exists():
        req = next(root.rglob("REQ-0002.yml"))
    req.write_text(req.read_text(encoding="utf-8").replace(
        "status: proposed", "status: draft"), encoding="utf-8")
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
    assert DISALLOWED in err
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
    assert item.status == "ratified" and item.ratifications[-1]["by"] == "Ada"


def test_a_draft_is_refused_where_ratification_leaves_the_status_alone(graph):
    """SR-0307: a project that declares ratification does not move the status
    (SR-0172) still does not sign a draft — it is agreed or deleted."""
    config = graph / "throughline.toml"
    text = config.read_text(encoding="utf-8")
    assert "[ratify]" not in text
    config.write_text(text + "\n[ratify]\nmoves_status = false\n", encoding="utf-8")
    before = _bytes(graph)
    assert _cli(["-C", graph, "ratify", "REQ-0002", "--by", "Ada"]) == 2
    assert _bytes(graph) == before


def test_the_refusal_is_the_one_the_write_would_raise(graph):
    """The early refusal and the write are one predicate (SR-0195), so the library
    call refuses in the same words the command prints."""
    from throughline.graph import Index
    from throughline.grounding import GroundingError, ratification_obstacle, ratify
    project = load_project(graph)
    item = project.get("REQ-0002")
    obstacle = ratification_obstacle(project.schema, Index.build(project), item)
    assert obstacle.startswith(DISALLOWED)
    assert "tl status REQ-0002 proposed" in obstacle
    with pytest.raises(GroundingError) as exc:
        ratify(project, "REQ-0002", by="Ada")
    assert str(exc.value) == obstacle
