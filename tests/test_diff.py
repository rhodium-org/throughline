# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""What changed between two states of a graph (SR-0037).

Nothing compared two states of a graph: a reviewer read the YAML diff of every
item file. `tl diff` reports, for two git revisions or a revision and the working
tree, each item added, removed or modified, with the fields that moved and the
links added and removed, as text and as JSON. A named state is a git tag.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.views import graph_diff
from throughline.storage import load_project, load_project_at_ref


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t",
                    "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
                    *args], check=True, capture_output=True)


@pytest.fixture
def graph(tmp_path) -> Path:
    """The starter graph committed and tagged `agreed`, then changed in four ways
    and committed again: an item added, one amended, a link added, one retired."""
    root = tmp_path / "demo"
    assert _cli(["-C", root, "init", "--name", "t"]) == 0
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "as agreed")
    _git(root, "tag", "agreed")
    assert _cli(["-C", root, "new", "REQ", "--title", "Second", "--ground",
                 "INT-0001", "--ground-type", "implements", "--no-interactive"]) == 0
    assert _cli(["-C", root, "amend", "REQ-0001", "--title", "First, reworded",
                 "--attr", "priority=should"]) == 0
    assert _cli(["-C", root, "link", "NFR-0001", "REQ-0001", "--type", "relates"]) == 0
    assert _cli(["-C", root, "delete", "NG-0001", "--reason", "no longer excluded"]) == 0
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "changed")
    return root


def test_items_added_removed_and_modified_are_each_reported(graph):
    """SR-0037: per item added, deleted, modified, and links added and removed."""
    was, _ = load_project_at_ref(graph, "agreed")
    diff = graph_diff(was, load_project(graph))

    assert [i["uid"] for i in diff["added"]] == ["REQ-0002"]
    assert diff["removed"] == []
    changed = {m["uid"]: m for m in diff["modified"]}
    assert set(changed) == {"REQ-0001", "NFR-0001", "NG-0001"}
    assert changed["REQ-0001"]["fields"] == ["title", "attrs.priority"]
    assert changed["REQ-0001"]["links_added"] == changed["REQ-0001"]["links_removed"] == []
    assert changed["NFR-0001"]["fields"] == []
    assert changed["NFR-0001"]["links_added"] == [{"target": "REQ-0001", "type": "relates"}]
    assert "status" in changed["NG-0001"]["fields"]


def test_an_item_whose_file_is_gone_is_removed(graph):
    """SR-0037."""
    (graph / "tests" / "TEST-0001.yml").unlink()
    was, _ = load_project_at_ref(graph, "HEAD")
    diff = graph_diff(was, load_project(graph))
    assert [i["uid"] for i in diff["removed"]] == ["TEST-0001"]
    assert diff["added"] == [] and diff["modified"] == []


def test_two_states_that_hold_the_same_items_differ_in_nothing(graph):
    was, _ = load_project_at_ref(graph, "HEAD")
    assert graph_diff(was, load_project(graph)) == {
        "added": [], "removed": [], "modified": []}


def test_a_retyped_link_is_one_removed_and_one_added(graph):
    """SR-0037: links added and removed."""
    assert _cli(["-C", graph, "link", "NFR-0001", "REQ-0001", "--type", "satisfies",
                 "--retype"]) == 0
    was, _ = load_project_at_ref(graph, "HEAD")
    (changed,) = graph_diff(was, load_project(graph))["modified"]
    assert changed["uid"] == "NFR-0001" and changed["fields"] == []
    assert changed["links_removed"] == [{"target": "REQ-0001", "type": "relates"}]
    assert changed["links_added"] == [{"target": "REQ-0001", "type": "satisfies"}]


def test_a_link_taken_away_is_removed(graph):
    """SR-0037."""
    assert _cli(["-C", graph, "unlink", "NFR-0001", "REQ-0001"]) == 0
    was, _ = load_project_at_ref(graph, "HEAD")
    (changed,) = graph_diff(was, load_project(graph))["modified"]
    assert changed["links_removed"] == [{"target": "REQ-0001", "type": "relates"}]
    assert changed["links_added"] == []


def test_the_command_compares_a_tag_with_the_working_tree(graph, capsys):
    """SR-0037: a named state, a revision, or working state; as text."""
    capsys.readouterr()
    assert _cli(["-C", graph, "diff", "agreed"]) == 0
    out = capsys.readouterr().out
    assert "+ REQ-0002" in out and "Second" in out
    assert "~ REQ-0001" in out and "title" in out and "attrs.priority" in out
    assert "~ NFR-0001" in out and "+relates REQ-0001" in out
    assert "1 added" in out and "0 removed" in out and "3 modified" in out


def test_the_command_compares_two_revisions(graph, capsys):
    """SR-0037: two revisions, whatever the working tree holds."""
    (graph / "tests" / "TEST-0001.yml").unlink()
    capsys.readouterr()
    assert _cli(["-C", graph, "diff", "agreed", "HEAD", "--format", "json"]) == 0
    diff = json.loads(capsys.readouterr().out)
    assert diff["from"]["revision"] == "agreed" and diff["to"]["revision"] == "HEAD"
    assert len(diff["from"]["commit"]) == 40
    assert [i["uid"] for i in diff["added"]] == ["REQ-0002"]
    assert diff["removed"] == [], "the working tree is not what was asked about"
    assert len(diff["modified"]) == 3


def test_the_working_tree_is_compared_with_the_last_commit_by_default(graph, capsys):
    assert _cli(["-C", graph, "amend", "REQ-0002", "--text", "Now with words."]) == 0
    capsys.readouterr()
    assert _cli(["-C", graph, "diff", "--format", "json"]) == 0
    diff = json.loads(capsys.readouterr().out)
    assert diff["from"]["revision"] == "HEAD" and diff["to"]["revision"] is None
    assert [(m["uid"], m["fields"]) for m in diff["modified"]] == [("REQ-0002", ["text"])]


def test_a_revision_that_cannot_be_read_is_refused(graph, capsys):
    capsys.readouterr()
    assert _cli(["-C", graph, "diff", "no-such-tag"]) == 2
    assert "no-such-tag" in capsys.readouterr().err


def test_comparing_writes_nothing(graph):
    before = {p: p.read_bytes() for p in graph.rglob("*")
              if p.is_file() and ".git" not in p.parts}
    assert _cli(["-C", graph, "diff", "agreed"]) == 0
    assert {p: p.read_bytes() for p in graph.rglob("*")
            if p.is_file() and ".git" not in p.parts} == before
