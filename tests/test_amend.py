# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""`tl amend` — change an item's content through the tool (SR-0144), report what
the change made stale (SR-0169), and refuse to write the ratification record
(SR-0170).

Before this command an item's title, text, rationale and attributes were
write-once at creation, so every later correction meant opening the YAML — the
manual editing the CLI exists to spare the author, and unsafe in a way the tool
cannot catch, because a colon followed by a space inside a plain scalar silently
reparses the field and surfaces much later as a loader error naming an unrelated
file. These tests hold the command to the three requirements, and drive it through
`main` rather than the functions beneath it, because the obligations are about what
an author at a terminal can and cannot do.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from throughline.cli import main as _cli
from throughline.fingerprint import fingerprint
from throughline.storage import load_project


def _scaffold(tmp_path) -> Path:
    """A bare project with an intent root and one grounded, normative requirement.

    The bare scaffold declares `priority` normative and `origin` not, which is the
    pair these tests need — one attribute whose change moves the fingerprint and
    one whose change does not."""
    root = tmp_path / "proj"
    assert _cli(["-C", str(root), "init", "--name", "t", "--bare"]) == 0
    assert _cli(["-C", str(root), "register", "new", "INT", "vision"]) == 0
    assert _cli(["-C", str(root), "register", "new", "FR", "features"]) == 0
    assert _cli(["-C", str(root), "new", "INT", "--type", "intent",
                 "--title", "why"]) == 0
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "original title", "--text", "original text",
                 "--ground", "INT-0001"]) == 0
    return root


def _item(root: Path, uid: str):
    return load_project(root).get(uid)


# --------------------------------------------------------------------------- #
# SR-0144 — the content an author may change, and the identity they may not
# --------------------------------------------------------------------------- #

def test_amend_title_leaves_every_other_field_alone(tmp_path):
    root = _scaffold(tmp_path)
    before = _item(root, "FR-0001")
    assert _cli(["-C", str(root), "amend", "FR-0001", "--title", "a better title"]) == 0
    after = _item(root, "FR-0001")
    assert after.title == "a better title"
    assert after.text == before.text
    assert after.type == before.type
    assert after.status == before.status
    assert [(l.target, l.type) for l in after.links] == \
           [(l.target, l.type) for l in before.links]


def test_amend_text_and_rationale(tmp_path):
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "amend", "FR-0001", "--text", "rewritten",
                 "--rationale", "because the first wording was wrong"]) == 0
    it = _item(root, "FR-0001")
    assert it.text == "rewritten"
    assert it.rationale == "because the first wording was wrong"


def test_an_explicitly_empty_value_clears_the_field(tmp_path):
    """The only way to withdraw a rationale without opening the YAML. Distinct from
    omitting the option, which leaves the field alone."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "amend", "FR-0001", "--rationale", "temporary"]) == 0
    assert _item(root, "FR-0001").rationale == "temporary"
    assert _cli(["-C", str(root), "amend", "FR-0001", "--rationale", ""]) == 0
    assert _item(root, "FR-0001").rationale == ""


def test_amend_with_no_field_given_is_refused(tmp_path):
    """Succeeding silently would let a typo in an option name read as a change that
    was made."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "amend", "FR-0001"]) == 2


def test_amend_a_nonexistent_item_is_refused(tmp_path):
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "amend", "FR-9999", "--title", "x"]) == 2


@pytest.mark.parametrize("option", ["--uid", "--type", "--status", "--links"])
def test_identity_and_the_verbs_that_own_it_are_not_amendable(tmp_path, option):
    """Obligation 3 is a refusal, not merely an absent feature — identity is
    immutable and status/links each have their own verb."""
    root = _scaffold(tmp_path)
    with pytest.raises(SystemExit) as e:
        _cli(["-C", str(root), "amend", "FR-0001", option, "whatever"])
    assert e.value.code == 2
    assert _item(root, "FR-0001").title == "original title"


def test_amending_to_the_value_it_already_has_changes_nothing(tmp_path, capsys):
    root = _scaffold(tmp_path)
    before = _item(root, "FR-0001")
    assert _cli(["-C", str(root), "amend", "FR-0001",
                 "--title", "original title"]) == 0
    assert "nothing changed" in capsys.readouterr().out
    assert fingerprint(_item(root, "FR-0001"), load_project(root).schema) == \
           fingerprint(before, load_project(root).schema)


# --------------------------------------------------------------------------- #
# SR-0144 — typed attributes, the same ones creation accepts
# --------------------------------------------------------------------------- #

def test_amend_a_declared_enum_attribute(tmp_path):
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "amend", "FR-0001", "--attr", "priority=must"]) == 0
    assert _item(root, "FR-0001").attrs["priority"] == "must"


def test_a_value_the_schema_cannot_accept_is_refused(tmp_path):
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "amend", "FR-0001",
                 "--attr", "priority=urgent"]) == 2
    assert "priority" not in _item(root, "FR-0001").attrs


def test_an_attribute_the_type_does_not_declare_is_refused_by_name(tmp_path, capsys):
    """Obligation 4. Creation stores an undeclared attribute verbatim; amend does
    not, because correcting an item is not the moment to invent a field."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "amend", "FR-0001", "--attr", "colour=blue"]) == 2
    err = capsys.readouterr().err
    assert "colour" in err and "requirement" in err
    assert "colour" not in _item(root, "FR-0001").attrs


# --------------------------------------------------------------------------- #
# SR-0144 / SR-0169 — what a content change costs, and what it leaves alone
# --------------------------------------------------------------------------- #

def _stamped_dependent(root: Path) -> None:
    """A second requirement whose link to FR-0001 is confirmed at its content now."""
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "dependent", "--ground", "INT-0001"]) == 0
    assert _cli(["-C", str(root), "link", "FR-0002", "FR-0001",
                 "--type", "refines", "--stamp"]) == 0


def test_changing_normative_text_makes_a_confirmed_dependent_suspect(tmp_path, capsys):
    root = _scaffold(tmp_path)
    _stamped_dependent(root)
    capsys.readouterr()
    assert _cli(["-C", str(root), "amend", "FR-0001", "--text", "materially different"]) == 0
    out = capsys.readouterr().out
    assert "FR-0002" in out, "the dependent it just invalidated was not named"
    # and the graph agrees, not just the message
    assert _cli(["-C", str(root), "check", "--strict"]) == 1
    assert "suspect" in capsys.readouterr().out.lower()


def test_retitling_disturbs_nothing(tmp_path, capsys):
    """The fingerprint covers text and normative attrs, not the title — so a
    retitle must not cascade, and the report must say so rather than stay silent."""
    root = _scaffold(tmp_path)
    _stamped_dependent(root)
    capsys.readouterr()
    assert _cli(["-C", str(root), "amend", "FR-0001", "--title", "clearer"]) == 0
    out = capsys.readouterr().out
    assert "unchanged" in out
    assert "FR-0002" not in out
    assert _cli(["-C", str(root), "check"]) == 0


def test_a_normative_attribute_cascades_and_a_non_normative_one_does_not(tmp_path, capsys):
    root = _scaffold(tmp_path)
    _stamped_dependent(root)
    capsys.readouterr()
    # origin is declared, but not normative
    assert _cli(["-C", str(root), "amend", "FR-0001", "--attr", "origin=human"]) == 0
    assert "unchanged" in capsys.readouterr().out
    # priority is declared normative, so it is part of what a stamp confirms
    assert _cli(["-C", str(root), "amend", "FR-0001", "--attr", "priority=must"]) == 0
    assert "FR-0002" in capsys.readouterr().out


def test_a_content_change_clears_the_review_record(tmp_path, capsys):
    """A review confirms content; content that has moved is no longer confirmed
    (SR-0038), and a stale confirmation left standing is worse than none."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "review", "FR-0001"]) == 0
    assert _item(root, "FR-0001").reviewed is not None
    capsys.readouterr()
    assert _cli(["-C", str(root), "amend", "FR-0001", "--text", "rewritten"]) == 0
    assert "review record cleared" in capsys.readouterr().out
    assert _item(root, "FR-0001").reviewed is None


def test_a_retitle_does_not_cost_the_author_a_re_review(tmp_path):
    """Clearing a review the change did not disturb would be friction for nothing."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "review", "FR-0001"]) == 0
    before = _item(root, "FR-0001").reviewed
    assert _cli(["-C", str(root), "amend", "FR-0001", "--title", "clearer"]) == 0
    assert _item(root, "FR-0001").reviewed == before


def test_amend_reports_that_a_ratification_no_longer_matches(tmp_path, capsys):
    """amend leaves the ratification record alone and lets the existing staleness
    machinery detect the drift — but says so, rather than letting the author find
    out at the next gate (SR-0169)."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "status", "FR-0001", "approved"]) == 0
    assert _cli(["-C", str(root), "ratify", "FR-0001", "--by", "Ada Lovelace"]) == 0
    capsys.readouterr()
    assert _cli(["-C", str(root), "amend", "FR-0001", "--text", "rewritten"]) == 0
    out = capsys.readouterr().out
    assert "Ada Lovelace" in out and "no longer matches" in out
    it = _item(root, "FR-0001")
    assert it.attrs["ratified_by"] == "Ada Lovelace", "the record was not touched"
    assert _cli(["-C", str(root), "check", "--strict"]) == 1
    assert "ratified-stale" in capsys.readouterr().out


def test_amend_never_refuses_on_account_of_how_much_it_affects(tmp_path):
    """SR-0169 — a statement of consequence, not a prompt. A ratified item with a
    confirmed dependent is the most expensive case there is, and it still succeeds
    without confirmation."""
    root = _scaffold(tmp_path)
    _stamped_dependent(root)
    assert _cli(["-C", str(root), "status", "FR-0001", "approved"]) == 0
    assert _cli(["-C", str(root), "ratify", "FR-0001", "--by", "Ada Lovelace"]) == 0
    assert _cli(["-C", str(root), "amend", "FR-0001", "--text", "rewritten"]) == 0


# --------------------------------------------------------------------------- #
# SR-0170 — only ratification writes the ratification record
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("attr", [
    "ratified_by", "ratified_id", "ratified_fingerprint", "ratified_backfilled",
])
def test_amend_refuses_to_write_the_ratification_record(tmp_path, capsys, attr):
    """Correcting a title and signing a name nobody gave must not be the same
    keystroke."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "amend", "FR-0001",
                 f"--attr={attr}=forged"]) == 2
    err = capsys.readouterr().err
    assert attr in err
    assert "ratify" in err or "migrate" in err, "the refusal did not name the owner"
    assert attr not in _item(root, "FR-0001").attrs


@pytest.mark.parametrize("attr", ["ratified_by", "ratified_fingerprint"])
def test_creation_refuses_to_write_the_ratification_record_too(tmp_path, attr):
    """SR-0170 binds every operation that sets attributes, not only amend — an item
    born carrying a signature nobody gave is the same forgery."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "new", "FR", "--type", "requirement",
                 "--title", "smuggled", f"--attr={attr}=forged"]) == 2


def test_a_genuine_ratification_still_writes_the_record(tmp_path):
    """The refusal must cost a real ratifier nothing."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "status", "FR-0001", "approved"]) == 0
    assert _cli(["-C", str(root), "ratify", "FR-0001", "--by", "Ada Lovelace"]) == 0
    it = _item(root, "FR-0001")
    assert it.attrs["ratified_by"] == "Ada Lovelace"
    assert it.attrs["ratified_fingerprint"].startswith("sha256:")


# --------------------------------------------------------------------------- #
# The hazard the command exists to remove
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("nasty", [
    "a title with: a colon and a space",
    'quotes "inside" it',
    "a trailing hash # like a comment",
    "- leading dash",
    "%YAML directive-looking",
    "  leading and trailing spaces  ",
])
def test_punctuation_that_breaks_hand_edited_yaml_round_trips(tmp_path, nasty):
    """The whole point of the command is that the author never opens the YAML, so
    the text that would break a hand edit is exactly what must survive one."""
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "amend", "FR-0001", "--text", nasty]) == 0
    assert _item(root, "FR-0001").text == nasty
    assert _cli(["-C", str(root), "check"]) == 0


def test_check_stays_green_after_an_amend(tmp_path):
    root = _scaffold(tmp_path)
    assert _cli(["-C", str(root), "amend", "FR-0001", "--title", "t",
                 "--text", "x", "--attr", "priority=should"]) == 0
    assert _cli(["-C", str(root), "check"]) == 0
