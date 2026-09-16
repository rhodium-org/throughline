# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""SR-0196 — correcting the ratifier recorded on an unpublished ratification.

A ratification record is a statement about a person, and the Tool could make that
statement wrong and then refuse to correct it: SR-0148 declines a second signature
over unchanged content and SR-0170 refuses the attributes to every other command.

The correction is scoped to a record that has never been committed, and that scope
is the requirement rather than a caveat on it. The Tool authenticates nobody — the
ratifier is a string the caller supplies — so a general power to replace would let
one person lift another's accountability off an item with a supported command. An
unpublished record needs no such trust, and version control can be asked whether a
record is unpublished.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.identity import RATIFICATION_ATTRS
from throughline.ratification import SUPERSEDED_ATTR, ratification_is_committed
from throughline.storage import load_project


def _declare_intent_origin(root: Path) -> None:
    """The scaffold declares `[types.intent]` (SR-0202), so the attribute these
    fixtures need is added through the tool rather than by appending a second
    table the config already holds."""
    assert _cli(["-C", root, "schema", "attr", "add", "intent", "origin",
                 "--kind", "enum", "--values", "human,ai,hybrid",
                 "--because", "the fixture records who authored each intent"]) == 0


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True).stdout


@pytest.fixture
def graph(tmp_path) -> Path:
    """A git-backed project with one grounded requirement, ratified under a
    misspelled name and not yet committed."""
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    _declare_intent_origin(root)
    _git(root, "init")
    _git(root, "config", "user.email", "a@e")
    _git(root, "config", "user.name", "Ada Lovelace")
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "Why",
                 "--text", "V.", "--origin", "human", "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "R", "--text",
                 "The Tool shall work.", "--ground", "INT-0001",
                 "--ground-type", "implements", "--origin", "ai",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "AAda Lovelace"]) == 0
    return root


def _commit(root: Path) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "ratified")


def _by(root: Path):
    a = load_project(root).get("REQ-0001").attrs
    return a.get("ratified_by"), a.get(SUPERSEDED_ATTR)


# --------------------------------------------------------- correcting a slip

def test_an_uncommitted_ratifier_can_be_corrected(graph):
    """The case that motivated this: a name typed by hand, caught before it was
    shared with anyone (SR-0196)."""
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Ada Lovelace",
                 "--replacing"]) == 0
    assert _by(graph) == ("Ada Lovelace", "AAda Lovelace")


def test_the_correction_names_what_it_replaced(graph):
    """A correction must not read as the original record — SR-0148's condition that
    an accountability record never changes without the graph showing that it did."""
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Ada Lovelace",
                 "--replacing"]) == 0
    item = load_project(graph).get("REQ-0001")
    assert item.attrs[SUPERSEDED_ATTR] == "AAda Lovelace"
    assert item.attrs["ratified_fingerprint"]          # still stamped


def test_without_the_flag_it_is_still_refused(graph):
    """SR-0148's refusal stays the default: a second signature over unchanged
    content accepts nothing unless it is declared to be a correction."""
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Ada Lovelace"]) == 2
    assert _by(graph) == ("AAda Lovelace", None)


def test_the_refusal_names_the_route(graph, capsys):
    """The author learns the route rather than only that the door is shut."""
    _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Ada Lovelace"])
    assert "--replacing" in capsys.readouterr().err


# ------------------------------------------- a published signature is out of reach

def test_a_committed_ratification_may_not_be_replaced(graph):
    """The whole point of the scope: the Tool authenticates nobody, so a published
    record must not be takeable by whoever runs the command next (SR-0196)."""
    _commit(graph)
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Bob Malory",
                 "--replacing"]) == 2
    assert _by(graph) == ("AAda Lovelace", None)       # untouched


def test_the_refusal_explains_why_rather_than_only_that(graph, capsys):
    _commit(graph)
    _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Bob Malory", "--replacing"])
    err = capsys.readouterr().err
    assert "in a commit" in err
    assert "may not be replaced" in err


def test_a_correction_can_itself_be_corrected_before_it_is_committed(graph):
    """Two slips in one sitting is still one unpublished record."""
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Adda Lovelace",
                 "--replacing"]) == 0
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Ada Lovelace",
                 "--replacing"]) == 0
    assert _by(graph) == ("Ada Lovelace", "Adda Lovelace")


def test_a_correction_after_the_content_moved_is_the_ordinary_path(graph):
    """Content that HAS moved needs no correction flag — that is re-ratification,
    and it behaves as it did before this requirement."""
    _commit(graph)
    assert _cli(["-C", graph, "amend", "REQ-0001", "--text", "Moved on."]) == 0
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Ada Lovelace",
                 "--accept-change"]) == 0
    by, superseded = _by(graph)
    assert by == "Ada Lovelace"
    assert superseded is None          # not a correction, so nothing superseded


# --------------------------------------------------- the question is asked, not guessed

def test_outside_a_work_tree_the_correction_is_refused(tmp_path):
    """Unestablished is refused, never assumed: a correction is permitted on
    evidence the record was never shared, and absence of evidence is not that."""
    root = tmp_path / "nogit"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    _declare_intent_origin(root)
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "W",
                 "--text", "V.", "--origin", "human", "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "R", "--text", "T.",
                 "--ground", "INT-0001", "--ground-type", "implements",
                 "--origin", "ai", "--no-interactive"]) == 0
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Typo"]) == 0
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Real",
                 "--replacing"]) == 2
    assert load_project(root).get("REQ-0001").attrs["ratified_by"] == "Typo"


def test_a_work_tree_with_no_commits_is_an_answer_not_a_gap(graph):
    """A tree holding no commits has published nothing, so the question has an
    answer and the correction is allowed — the bug this test pins."""
    project = load_project(graph)
    assert ratification_is_committed(project, project.get("REQ-0001")) is False


def test_committed_state_is_read_from_the_commit_not_the_working_tree(graph):
    """The pair (ratifier, stamp) is what makes it the same record, so a signature
    taken, replaced and re-taken in one sitting does not read as published because
    an earlier one was."""
    _commit(graph)
    project = load_project(graph)
    assert ratification_is_committed(project, project.get("REQ-0001")) is True

    # A correction cannot land now, so force a different record the honest way:
    # content moves, which is ordinary re-ratification.
    assert _cli(["-C", graph, "amend", "REQ-0001", "--text", "Different."]) == 0
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Ada Lovelace",
                 "--accept-change"]) == 0
    project = load_project(graph)
    # The new record differs from the committed one in both halves of the pair.
    assert ratification_is_committed(project, project.get("REQ-0001")) is False


def test_an_item_with_no_ratification_has_published_none(graph):
    project = load_project(graph)
    assert ratification_is_committed(project, project.get("INT-0001")) is False


# ------------------------------------------------------------- the record is guarded

def test_the_superseded_field_is_owned_by_ratify(graph):
    """A hand-written predecessor would claim that one signature succeeded another
    when none did, so it is guarded like the rest of the record (SR-0170)."""
    assert RATIFICATION_ATTRS[SUPERSEDED_ATTR] == "ratify"
    assert _cli(["-C", graph, "amend", "REQ-0001",
                 "--attr", f"{SUPERSEDED_ATTR}=Bob Malory"]) == 2
    assert SUPERSEDED_ATTR not in load_project(graph).get("REQ-0001").attrs


def test_correcting_to_the_same_name_records_no_predecessor(graph):
    """Nothing succeeded anything, so the record gains no claim that it did."""
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "AAda Lovelace",
                 "--replacing"]) == 0
    assert SUPERSEDED_ATTR not in load_project(graph).get("REQ-0001").attrs
