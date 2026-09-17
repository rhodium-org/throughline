# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Removing or retyping a link never leaves the graph ungrounded (SR-0211), and the
link operations accept a wider graph to judge against (SR-0212).

Removing an item's last grounding link used to succeed and leave `tl check` red
(issue #3), and a composing tool kept its own copy of adding a link, which drifted.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.graph import Index
from throughline.links import GroundingView, LinkError, add_link, remove_link
from throughline.storage import load_project


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


@pytest.fixture
def graph(tmp_path) -> Path:
    """INT-0001 is served by REQ-0001 and REQ-0003; INT-0002 by REQ-0003 alone.
    REQ-0002 is grounded only through REQ-0001."""
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    for title in ("One", "Two"):
        assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", title,
                     "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "R1", "--ground", "INT-0001",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "R2", "--ground", "REQ-0001",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "R3", "--ground", "INT-0001",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "link", "REQ-0003", "INT-0002", "--type", "derives_from"]) == 0
    return root


def _bytes(root: Path, uid: str) -> bytes:
    return next(root.rglob(f"{uid}.yml")).read_bytes()


def test_removing_the_last_grounding_link_is_refused_naming_all_it_would_orphan(graph, capsys):
    before = _bytes(graph, "REQ-0001")
    assert _cli(["-C", graph, "unlink", "REQ-0001", "INT-0001"]) == 2
    err = capsys.readouterr().err
    assert "leave REQ-0001, REQ-0002 reaching no root" in err
    assert "nothing was changed" in err
    assert _bytes(graph, "REQ-0001") == before


def test_leaving_a_delivery_root_unserved_is_refused(graph, capsys):
    assert _cli(["-C", graph, "unlink", "REQ-0003", "INT-0002"]) == 2
    err = capsys.readouterr().err
    assert "leave INT-0002 served by nothing" in err
    assert "reaching no root" not in err


def test_linking_the_replacement_first_lets_the_old_link_go(graph):
    assert _cli(["-C", graph, "link", "REQ-0001", "INT-0002", "--type", "derives_from"]) == 0
    assert _cli(["-C", graph, "unlink", "REQ-0001", "INT-0001"]) == 0
    assert not [l for l in load_project(graph).get("REQ-0001").links
                if l.target == "INT-0001"]


def test_retyping_the_last_grounding_link_to_one_that_grounds_nothing_is_refused(graph, capsys):
    assert _cli(["-C", graph, "link", "REQ-0001", "INT-0001", "--type", "relates",
                 "--retype"]) == 2
    assert "reaching no root" in capsys.readouterr().err
    assert _cli(["-C", graph, "link", "REQ-0001", "INT-0001", "--type", "implements",
                 "--retype"]) == 0


def test_a_link_that_grounds_nothing_is_always_removable(graph):
    assert _cli(["-C", graph, "link", "REQ-0002", "INT-0002", "--type", "relates"]) == 0
    assert _cli(["-C", graph, "unlink", "REQ-0002", "INT-0002"]) == 0


def test_a_graph_red_elsewhere_does_not_block_an_unrelated_removal(graph):
    assert _cli(["-C", graph, "new", "REQ", "--title", "orphan", "--no-interactive"]) == 0
    assert _cli(["-C", graph, "link", "REQ-0002", "INT-0002", "--type", "relates"]) == 0
    assert _cli(["-C", graph, "unlink", "REQ-0002", "INT-0002"]) == 0


def test_a_wider_view_judges_the_removal(graph):
    """What a composing tool passes: here the wider graph also grounds REQ-0001 in
    INT-0002, so a removal refused over the project alone is allowed."""
    project = load_project(graph)
    with pytest.raises(LinkError):
        remove_link(project, "REQ-0001", "INT-0001")
    wider = Index.build(project).with_edges("REQ-0001", add=[("INT-0002", "derives_from")])
    assert remove_link(project, "REQ-0001", "INT-0001",
                       view=GroundingView(wider, project.get)) == ["derives_from"]


def test_adding_through_a_view_restamps_in_place_rather_than_duplicating(graph, tmp_path):
    """A target found only in the wider graph, as a borrowed clause is: a second
    --stamp refreshes the edge instead of adding another."""
    other = tmp_path / "source"
    assert _cli(["-C", other, "init", "--no-demo"]) == 0
    assert _cli(["-C", other, "new", "INT", "--type", "intent", "--title", "Borrowed",
                 "--no-interactive"]) == 0
    source = load_project(other)
    project = load_project(graph)

    def find(target: str):
        return source.get(target.split(":", 1)[1]) if target.startswith("src:") \
            else project.get(target)

    view = GroundingView(Index.build(project), find)
    assert add_link(project, "REQ-0003", "src:INT-0001", "relates", stamp=True,
                    view=view) == "linked"
    assert add_link(project, "REQ-0003", "src:INT-0001", "relates", stamp=True,
                    view=view) == "restamped"
    with pytest.raises(LinkError, match="already exists"):
        add_link(project, "REQ-0003", "src:INT-0001", "relates", view=view)
    edges = [l for l in load_project(graph).get("REQ-0003").links
             if l.target == "src:INT-0001"]
    assert len(edges) == 1 and edges[0].stamp
