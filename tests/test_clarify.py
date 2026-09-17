# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""SR-0213 — clarify removes an item's ambiguity flag and records who removed it and
why; SR-0214 — ratify shows that record before asking for the signature.

A flagged item cannot be ratified and fails `check --strict`, and before this nothing
in the tool removed the flag: `tl amend --unset` refuses, because removing the flag
does not clarify anything (SR-0206). Clarifying is a judgement that the ambiguity is
resolved, so it is recorded with a name and a reason, and it accepts nothing — the
person who later signs the item is shown the record.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.fingerprint import fingerprint
from throughline.grounding import (
    CLARIFICATION_ATTRS,
    CLARIFIED_AMBIGUITY_ATTR,
    CLARIFIED_BY_ATTR,
    CLARIFIED_REASON_ATTR,
    GroundingError,
    clarify,
)
from throughline.storage import load_project
from throughline.validate import validate

RECORD = [CLARIFIED_BY_ATTR, CLARIFIED_REASON_ATTR, CLARIFIED_AMBIGUITY_ATTR]


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _item(root: Path, uid: str):
    return load_project(root).get(uid)


def _file(root: Path, uid: str) -> Path:
    return next(root.rglob(f"{uid}.yml"))


def _flag(root: Path, uid: str, reason: str, by: str = "Scout") -> None:
    """Flag ``uid`` the way a reviewer does (SR-0221)."""
    assert _cli(["-C", root, "flag", uid, "--reason", reason, "--by", by]) == 0


def _ambiguous_finding(root: Path, uid: str) -> str | None:
    found = [f for f in validate(load_project(root))
             if f.uid == uid and f.rule == "ambiguous"]
    return found[0].message if found else None


@pytest.fixture
def graph(tmp_path) -> Path:
    """An intent, a machine-authored requirement a reviewer has flagged, a second
    requirement linked to the first through a stamped link, and a machine-authored
    one never flagged."""
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "Why",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "Fast", "--text",
                 "The Tool shall be fast.", "--origin", "ai", "--attr", "priority=must",
                 "--ground", "INT-0001", "--no-interactive"]) == 0
    assert _cli(["-C", root, "amend", "REQ-0001", "--rationale", "Users wait."]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "Dependent", "--text",
                 "The Tool shall cache.", "--ground", "INT-0001",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "link", "REQ-0002", "REQ-0001", "--type", "relates",
                 "--stamp"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "Plain", "--text",
                 "The Tool shall log.", "--origin", "ai", "--ground", "INT-0001",
                 "--no-interactive"]) == 0
    _flag(root, "REQ-0001", "'fast' is not measurable")
    return root


# ----------------------------------------------------------- what clarify does

def test_clarify_removes_the_flag_and_records_who_why_and_what_check_reported(graph):
    reported = _ambiguous_finding(graph, "REQ-0001")
    assert reported == "flagged ambiguous by Scout: 'fast' is not measurable"
    assert _cli(["-C", graph, "clarify", "REQ-0001", "--reason",
                 "reworded to a 200 ms limit", "--by", "Ada Lovelace"]) == 0
    a = _item(graph, "REQ-0001").attrs
    assert "ambiguous" not in a
    assert a[CLARIFIED_BY_ATTR] == "Ada Lovelace"
    assert a[CLARIFIED_REASON_ATTR] == "reworded to a 200 ms limit"
    # The record holds the words check showed, by construction the same wording.
    assert a[CLARIFIED_AMBIGUITY_ATTR] == reported
    assert _ambiguous_finding(graph, "REQ-0001") is None


def test_clarify_changes_nothing_else_on_the_item_or_the_graph(graph):
    project = load_project(graph)
    before = project.get("REQ-0001")
    was = (before.title, before.text, before.rationale, before.status,
           [(l.target, l.type, l.stamp) for l in before.links],
           {k: v for k, v in before.attrs.items() if k != "ambiguous"},
           fingerprint(before, project.schema))
    others = {uid: _file(graph, uid).read_bytes()
              for uid in ("INT-0001", "REQ-0002", "REQ-0003")}
    assert _cli(["-C", graph, "clarify", "REQ-0001", "--reason", "r",
                 "--by", "Ada"]) == 0
    project = load_project(graph)
    after = project.get("REQ-0001")
    now = (after.title, after.text, after.rationale, after.status,
           [(l.target, l.type, l.stamp) for l in after.links],
           {k: v for k, v in after.attrs.items() if k not in RECORD},
           fingerprint(after, project.schema))
    assert now == was
    assert {uid: _file(graph, uid).read_bytes() for uid in others} == others


def test_a_flag_set_at_creation_is_recorded_as_check_reports_it(graph):
    assert _cli(["-C", graph, "new", "REQ", "--title", "Vague", "--attr",
                 "ambiguous=true", "--ground", "INT-0001", "--no-interactive"]) == 0
    assert _cli(["-C", graph, "clarify", "REQ-0004", "--reason", "flag was wrong",
                 "--by", "Ada"]) == 0
    recorded = _item(graph, "REQ-0004").attrs[CLARIFIED_AMBIGUITY_ATTR]
    assert "no reason recorded" in recorded and "tl flag REQ-0004" in recorded


def test_a_clarified_item_can_be_ratified(graph, capsys):
    """Clarifying accepts nothing — the item is still awaiting a human — but it no
    longer blocks the one who signs."""
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Ada"]) == 2
    assert "flagged ambiguous" in capsys.readouterr().err
    assert _cli(["-C", graph, "clarify", "REQ-0001", "--reason", "r",
                 "--by", "Bob"]) == 0
    item = _item(graph, "REQ-0001")
    assert item.status == "proposed" and "ratified_by" not in item.attrs
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    assert _item(graph, "REQ-0001").attrs["ratified_by"] == "Ada"


def test_flagging_a_ratified_item_leaves_its_status_and_signature_alone(tmp_path):
    """SR-0221 leaves the status where it is, so a flag that turns out to be wrong
    costs nobody a second signature. The flag still blocks ratification while it
    stands."""
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "Why",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "R", "--text", "T.", "--origin",
                 "ai", "--ground", "INT-0001", "--no-interactive"]) == 0
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    stamp = _item(root, "REQ-0001").attrs["ratified_fingerprint"]
    _flag(root, "REQ-0001", "'T' is undefined")
    item = _item(root, "REQ-0001")
    assert item.status == "ratified"
    assert item.attrs["ratified_by"] == "Ada" and item.attrs["ratified_fingerprint"] == stamp
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada"]) == 2
    assert _cli(["-C", root, "clarify", "REQ-0001", "--reason", "defined in the "
                 "glossary", "--by", "Bob"]) == 0
    item = _item(root, "REQ-0001")
    assert item.status == "ratified"
    assert item.attrs["ratified_fingerprint"] == stamp     # the signature still holds


def test_a_flag_raised_again_is_clarified_again_and_the_record_replaced(graph):
    assert _cli(["-C", graph, "clarify", "REQ-0001", "--reason", "first",
                 "--by", "Ada"]) == 0
    _flag(graph, "REQ-0001", "'limit' is unbounded")
    assert _item(graph, "REQ-0001").attrs["ambiguous"] is True
    assert _cli(["-C", graph, "clarify", "REQ-0001", "--reason", "second",
                 "--by", "Bob"]) == 0
    a = _item(graph, "REQ-0001").attrs
    assert (a[CLARIFIED_BY_ATTR], a[CLARIFIED_REASON_ATTR]) == ("Bob", "second")
    assert "'limit' is unbounded" in a[CLARIFIED_AMBIGUITY_ATTR]


def test_the_brief_describes_clarify(graph, capsys):
    assert _cli(["-C", graph, "context"]) == 0
    assert "tl clarify <UID> --reason <why> --by <who>" in capsys.readouterr().out


# ------------------------------------------------------------------ refusals

@pytest.mark.parametrize("uid, says", [
    ("REQ-0003", "carries no ambiguity flag to remove"),
    ("REQ-0099", "REQ-0099 does not exist"),
])
def test_clarify_refuses_an_item_it_cannot_clarify(graph, capsys, uid, says):
    files = {p: p.read_bytes() for p in graph.rglob("*.yml")}
    assert _cli(["-C", graph, "clarify", uid, "--reason", "r", "--by", "Ada"]) == 2
    assert says in capsys.readouterr().err
    assert {p: p.read_bytes() for p in graph.rglob("*.yml")} == files


def test_clarify_refuses_a_tombstone(graph, capsys):
    assert _cli(["-C", graph, "delete", "REQ-0001", "--reason", "gone"]) == 0
    capsys.readouterr()
    assert _cli(["-C", graph, "clarify", "REQ-0001", "--reason", "r",
                 "--by", "Ada"]) == 2
    assert "tombstone is permanent" in capsys.readouterr().err


@pytest.mark.parametrize("argv, says", [
    (["--by", "Ada"], "pass --reason"),
    (["--reason", "r"], "pass --by"),
])
def test_clarify_needs_a_reason_and_a_name_when_nobody_can_be_asked(graph, capsys,
                                                                   argv, says):
    assert _cli(["-C", graph, "clarify", "REQ-0001", *argv]) == 2
    assert says in capsys.readouterr().err
    assert _item(graph, "REQ-0001").attrs["ambiguous"] is True


def test_an_empty_reason_or_name_is_refused_at_the_library(graph):
    for by, reason in (("Ada", "  "), ("", "r")):
        with pytest.raises(GroundingError):
            clarify(load_project(graph), "REQ-0001", by=by, reason=reason)


def test_clarify_takes_one_item_per_run(graph, capsys):
    """Each flag carries its own reasons, so one reason cannot serve a batch."""
    with pytest.raises(SystemExit) as exc:
        _cli(["-C", graph, "clarify", "REQ-0001", "REQ-0003", "--reason", "r",
              "--by", "Ada"])
    assert exc.value.code == 2


def test_an_unclarifiable_item_is_refused_before_anyone_is_asked(graph, monkeypatch,
                                                                capsys):
    import throughline.cli as climod
    monkeypatch.setattr(climod, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a, **k: pytest.fail("asked"))
    assert _cli(["-C", graph, "clarify", "REQ-0003"]) == 2
    assert "carries no ambiguity flag" in capsys.readouterr().err


def test_a_terminal_is_asked_for_the_reason_and_offered_the_signing_identity(
        graph, monkeypatch):
    import throughline.cli as climod
    monkeypatch.setattr(climod, "_interactive", lambda: True)
    monkeypatch.setattr(climod, "default_ratifier", lambda path=None: "Ada Lovelace")
    answers = iter(["the flag was wrong", ""])          # reason, then take the default
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    assert _cli(["-C", graph, "clarify", "REQ-0001"]) == 0
    a = _item(graph, "REQ-0001").attrs
    assert (a[CLARIFIED_BY_ATTR], a[CLARIFIED_REASON_ATTR]) == (
        "Ada Lovelace", "the flag was wrong")


# ------------------------------------- no other operation removes the flag or the record

def test_amend_unset_refuses_the_flag_and_names_clarify(graph, capsys):
    before = _file(graph, "REQ-0001").read_bytes()
    assert _cli(["-C", graph, "amend", "REQ-0001", "--unset", "ambiguous"]) == 2
    err = capsys.readouterr().err
    assert "removing the flag does not clarify it" in err and "tl clarify" in err
    assert _file(graph, "REQ-0001").read_bytes() == before


def test_a_false_value_is_not_the_flag_and_may_be_removed(graph):
    """Only a value that flags the item is the flag; a false one blocks nothing, and
    clarify would refuse it, so refusing its removal too would leave no route."""
    assert _cli(["-C", graph, "schema", "attr", "add", "requirement", "ambiguous",
                 "--kind", "bool", "--because", "reviewers flag items by hand"]) == 0
    assert _cli(["-C", graph, "amend", "REQ-0003", "--attr", "ambiguous=false"]) == 0
    assert _cli(["-C", graph, "amend", "REQ-0003", "--unset", "ambiguous"]) == 0
    assert "ambiguous" not in _item(graph, "REQ-0003").attrs


def test_a_declared_flag_cannot_be_set_false_on_a_flagged_item(graph, capsys):
    """Declaring the attribute used to open a second route that recorded nothing."""
    assert _cli(["-C", graph, "schema", "attr", "add", "requirement", "ambiguous",
                 "--kind", "bool", "--because", "reviewers flag items by hand"]) == 0
    capsys.readouterr()
    before = _file(graph, "REQ-0001").read_bytes()
    assert _cli(["-C", graph, "amend", "REQ-0001", "--attr", "ambiguous=false"]) == 2
    assert "tl clarify" in capsys.readouterr().err
    assert _file(graph, "REQ-0001").read_bytes() == before
    # Raising a flag by hand is not removing one.
    assert _cli(["-C", graph, "amend", "REQ-0003", "--attr", "ambiguous=true"]) == 0
    assert _item(graph, "REQ-0003").attrs["ambiguous"] is True


def test_withdrawing_a_declared_flag_with_its_values_refuses_a_flagged_carrier(graph,
                                                                               capsys):
    assert _cli(["-C", graph, "schema", "attr", "add", "requirement", "ambiguous",
                 "--kind", "bool", "--because", "reviewers flag items by hand"]) == 0
    capsys.readouterr()
    assert _cli(["-C", graph, "schema", "attr", "remove", "requirement", "ambiguous",
                 "--unset", "--because", "no longer used"]) == 2
    assert "tl clarify" in capsys.readouterr().err
    assert _item(graph, "REQ-0001").attrs["ambiguous"] is True


@pytest.mark.parametrize("name", RECORD)
def test_the_clarification_record_is_owned_by_clarify(graph, name, capsys):
    """A hand-written record would claim that somebody judged an ambiguity resolved
    when nobody did (SR-0170 applied to SR-0213)."""
    assert CLARIFICATION_ATTRS[name] == "clarify"
    assert _cli(["-C", graph, "amend", "REQ-0003", "--attr", f"{name}=x"]) == 2
    assert _cli(["-C", graph, "new", "REQ", "--title", "X", "--attr", f"{name}=x",
                 "--ground", "INT-0001", "--no-interactive"]) == 2
    err = capsys.readouterr().err
    assert "part of the clarification record" in err and "tl clarify" in err
    assert _cli(["-C", graph, "clarify", "REQ-0001", "--reason", "r",
                 "--by", "Ada"]) == 0
    capsys.readouterr()
    assert _cli(["-C", graph, "amend", "REQ-0001", "--unset", name]) == 2
    assert "tl clarify" in capsys.readouterr().err
    assert name in _item(graph, "REQ-0001").attrs


def test_the_ratification_record_refusal_is_worded_as_before(graph, capsys):
    assert _cli(["-C", graph, "amend", "REQ-0003", "--attr", "ratified_by=x"]) == 2
    assert ("'ratified_by' is part of the ratification record and cannot be set by "
            "`tl amend` — `tl ratify` owns it") in capsys.readouterr().err


# ----------------------------------------------- SR-0214: ratify shows the record

def _tty(monkeypatch, answers):
    """An interactive terminal whose prompts leave a marker in the stream the
    rendering writes to, so a test can tell what came before the question."""
    import throughline.cli as climod
    monkeypatch.setattr(climod, "_interactive", lambda: True)
    supply = iter(answers)

    def _input(*_a, **_k):
        print("<<asked>>", file=sys.stderr)
        return next(supply)

    monkeypatch.setattr("builtins.input", _input)


def test_ratify_shows_who_removed_the_flag_why_and_what_was_reported(graph,
                                                                    monkeypatch,
                                                                    capsys):
    assert _cli(["-C", graph, "clarify", "REQ-0001", "--reason",
                 "reworded to a 200 ms limit", "--by", "Bob Malory"]) == 0
    capsys.readouterr()
    _tty(monkeypatch, ["y"])
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    err = capsys.readouterr().err
    for shown in ("removed by: Bob Malory", "reason: reworded to a 200 ms limit",
                  "check had reported: flagged ambiguous by Scout: "
                  "'fast' is not measurable"):
        assert shown in err, shown
        assert err.index(shown) < err.index("<<asked>>")


def test_an_item_never_clarified_is_shown_as_before(graph, monkeypatch, capsys):
    _tty(monkeypatch, ["y"])
    assert _cli(["-C", graph, "ratify", "REQ-0003", "--by", "Ada"]) == 0
    err = capsys.readouterr().err
    assert "The Tool shall log." in err
    assert "clarified" not in err and "removed by" not in err


def test_a_run_without_a_terminal_shows_nothing(graph, capsys):
    assert _cli(["-C", graph, "clarify", "REQ-0001", "--reason", "r",
                 "--by", "Bob"]) == 0
    capsys.readouterr()
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    assert "removed by" not in capsys.readouterr().err
