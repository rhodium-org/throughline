# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""SR-0221 — flag marks an item as ambiguous, with who flagged it and why;
SR-0222 — check reports a flagged item and ratify refuses it until it is clarified;
SR-0223 — a flag set when an item is created is read as true or false.

Check reported a flagged item, ratify refused it and `tl clarify` removed the flag,
but nothing raised one: the only code that set it was `scout_ingest`, a library
function with no command, no caller and no requirement behind it. It is gone, and a
reviewer flags an item through the tool, naming themselves and saying why.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import throughline
from throughline.cli import main as cli_main
from throughline.fingerprint import fingerprint
from throughline.grounding import GroundingError, flag
from throughline.storage import load_project


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _item(root: Path, uid: str):
    return load_project(root).get(uid)


def _file(root: Path, uid: str) -> Path:
    return next(root.rglob(f"{uid}.yml"))


def _bytes(root: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in sorted(root.rglob("*.yml"))}


def _findings(root: Path, uid: str, rule: str, capsys) -> list[dict]:
    capsys.readouterr()
    _cli(["-C", root, "check", "--format", "json"])
    out = capsys.readouterr().out
    return [f for f in json.loads(out) if f["uid"] == uid and f["rule"] == rule]


@pytest.fixture
def graph(tmp_path) -> Path:
    """An intent, a machine-authored requirement with a rationale, and a second one
    linked to it through a stamped link."""
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "Why",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "Fast", "--text",
                 "The Tool shall be fast.", "--origin", "ai", "--attr",
                 "priority=must", "--ground", "INT-0001", "--no-interactive"]) == 0
    assert _cli(["-C", root, "amend", "REQ-0001", "--rationale", "Users wait."]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "Dependent", "--text",
                 "The Tool shall cache.", "--origin", "ai", "--ground", "INT-0001",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "link", "REQ-0002", "REQ-0001", "--type", "relates",
                 "--stamp"]) == 0
    return root


# ------------------------------------------------------------ what flag does

def test_flag_records_the_flag_with_who_raised_it_and_why(graph, capsys):
    assert _cli(["-C", graph, "flag", "REQ-0001", "--reason",
                 "'fast' is not measurable", "--by", "Ada Lovelace"]) == 0
    assert "REQ-0001 flagged ambiguous by Ada Lovelace" in capsys.readouterr().out
    attrs = _item(graph, "REQ-0001").attrs
    assert attrs["ambiguous"] is True
    assert attrs["suspect_reasons"] == [
        "flagged ambiguous by Ada Lovelace: 'fast' is not measurable"]


def test_flag_changes_nothing_else_on_the_item_or_the_graph(graph):
    project = load_project(graph)
    before = project.get("REQ-0001")
    was = (before.title, before.text, before.rationale, before.status,
           [(l.target, l.type, l.stamp) for l in before.links],
           dict(before.attrs), fingerprint(before, project.schema))
    others = {uid: _file(graph, uid).read_bytes() for uid in ("INT-0001", "REQ-0002")}
    assert _cli(["-C", graph, "flag", "REQ-0001", "--reason", "r", "--by", "Ada"]) == 0
    project = load_project(graph)
    after = project.get("REQ-0001")
    now = (after.title, after.text, after.rationale, after.status,
           [(l.target, l.type, l.stamp) for l in after.links],
           {k: v for k, v in after.attrs.items()
            if k not in ("ambiguous", "suspect_reasons")},
           fingerprint(after, project.schema))
    assert now == was
    assert {uid: _file(graph, uid).read_bytes() for uid in others} == others


def test_a_second_flag_keeps_the_flag_and_adds_its_reason(graph, capsys):
    assert _cli(["-C", graph, "flag", "REQ-0001", "--reason", "'fast' is vague",
                 "--by", "Ada"]) == 0
    assert _cli(["-C", graph, "flag", "REQ-0001", "--reason", "'the Tool' is which?",
                 "--by", "Bob"]) == 0
    attrs = _item(graph, "REQ-0001").attrs
    assert attrs["ambiguous"] is True
    assert attrs["suspect_reasons"] == [
        "flagged ambiguous by Ada: 'fast' is vague",
        "flagged ambiguous by Bob: 'the Tool' is which?"]
    reported = _findings(graph, "REQ-0001", "ambiguous", capsys)[0]["message"]
    assert reported == "; ".join(attrs["suspect_reasons"])


def test_a_flagged_item_is_refused_by_ratify_and_returns_after_clarifying(graph,
                                                                         capsys):
    assert _cli(["-C", graph, "flag", "REQ-0001", "--reason", "vague",
                 "--by", "Ada"]) == 0
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Bob"]) == 2
    assert "cannot be ratified until clarified" in capsys.readouterr().err
    assert _cli(["-C", graph, "clarify", "REQ-0001", "--reason", "reworded",
                 "--by", "Bob"]) == 0
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Bob"]) == 0


def test_the_grounding_operations_are_exported_from_the_package():
    """SR-0061: the package's public list is what it offers as its library API, so
    the operations the CLI offers are on it."""
    for name in ("flag", "clarify", "withdraw", "ratify", "invalidate"):
        assert name in throughline.__all__
        assert getattr(throughline, name) is not None


# ------------------------------------------------------------------ refusals

@pytest.mark.parametrize("uid, says", [
    ("REQ-0099", "REQ-0099 does not exist"),
    ("INT-0001", None),                      # a root is flaggable like anything else
])
def test_flag_refuses_only_what_it_cannot_flag(graph, capsys, uid, says):
    rc = _cli(["-C", graph, "flag", uid, "--reason", "r", "--by", "Ada"])
    if says is None:
        assert rc == 0 and _item(graph, uid).attrs["ambiguous"] is True
    else:
        assert rc == 2 and says in capsys.readouterr().err


def test_flag_refuses_a_tombstone(graph, capsys):
    assert _cli(["-C", graph, "delete", "REQ-0001", "--reason", "gone"]) == 0
    capsys.readouterr()
    assert _cli(["-C", graph, "flag", "REQ-0001", "--reason", "r", "--by", "A"]) == 2
    assert "tombstone is permanent" in capsys.readouterr().err


@pytest.mark.parametrize("argv, says", [
    (["--by", "Ada"], "pass --reason"),
    (["--reason", "r"], "pass --by"),
])
def test_flag_needs_a_reason_and_a_name_when_nobody_can_be_asked(graph, capsys,
                                                                argv, says):
    before = _bytes(graph)
    assert _cli(["-C", graph, "flag", "REQ-0001", *argv]) == 2
    assert says in capsys.readouterr().err
    assert _bytes(graph) == before


def test_an_empty_reason_or_name_is_refused_at_the_library(graph):
    for by, reason in (("Ada", "  "), ("", "r")):
        with pytest.raises(GroundingError):
            flag(load_project(graph), "REQ-0001", by=by, reason=reason)


def test_flag_takes_one_item_per_run(graph):
    with pytest.raises(SystemExit) as exc:
        _cli(["-C", graph, "flag", "REQ-0001", "REQ-0002", "--reason", "r",
              "--by", "Ada"])
    assert exc.value.code == 2


def test_an_unflaggable_item_is_refused_before_anyone_is_asked(graph, monkeypatch,
                                                              capsys):
    import throughline.cli as climod
    monkeypatch.setattr(climod, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a, **k: pytest.fail("asked"))
    assert _cli(["-C", graph, "flag", "REQ-0099"]) == 2
    assert "does not exist" in capsys.readouterr().err


def test_a_terminal_is_asked_for_the_reason_and_offered_the_signing_identity(
        graph, monkeypatch):
    import throughline.cli as climod
    monkeypatch.setattr(climod, "_interactive", lambda: True)
    monkeypatch.setattr(climod, "default_ratifier", lambda path=None: "Ada Lovelace")
    answers = iter(["'fast' is not measurable", ""])   # reason, then take the default
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    assert _cli(["-C", graph, "flag", "REQ-0001"]) == 0
    assert _item(graph, "REQ-0001").attrs["suspect_reasons"] == [
        "flagged ambiguous by Ada Lovelace: 'fast' is not measurable"]


def test_the_brief_describes_flag(graph, capsys):
    assert _cli(["-C", graph, "context"]) == 0
    assert "tl flag <UID> --reason <why> --by <who>" in capsys.readouterr().out


# ------------------------------- SR-0222: what check says about a flagged item

def test_the_finding_is_a_warning_and_can_be_switched_off(graph, capsys):
    assert _cli(["-C", graph, "flag", "REQ-0001", "--reason", "vague",
                 "--by", "Ada"]) == 0
    found = _findings(graph, "REQ-0001", "ambiguous", capsys)
    assert len(found) == 1 and found[0]["severity"] == "warning"
    config = graph / "throughline.toml"
    config.write_text(config.read_text(encoding="utf-8") + '\n[rules]\nambiguous = "off"\n',
                      encoding="utf-8")
    assert _findings(graph, "REQ-0001", "ambiguous", capsys) == []


def test_a_flag_with_no_reason_says_so_and_names_the_operation(graph, capsys):
    """A flag raised when the item was created carries no reason (SR-0223), and
    "flagged ambiguous" alone left the author nothing to act on."""
    assert _cli(["-C", graph, "new", "REQ", "--title", "Vague", "--text", "V.",
                 "--attr", "ambiguous=true", "--ground", "INT-0001",
                 "--no-interactive"]) == 0
    message = _findings(graph, "REQ-0003", "ambiguous", capsys)[0]["message"]
    assert "no reason recorded" in message
    assert "tl flag REQ-0003 --reason" in message
    # And a reason, once recorded, replaces it.
    assert _cli(["-C", graph, "flag", "REQ-0003", "--reason", "which V?",
                 "--by", "Ada"]) == 0
    assert _findings(graph, "REQ-0003", "ambiguous", capsys)[0]["message"] == (
        "flagged ambiguous by Ada: which V?")


# ------------------------------- SR-0223: a flag given at creation is a boolean

def test_a_false_flag_at_creation_does_not_flag_the_item(graph, capsys):
    assert _cli(["-C", graph, "new", "REQ", "--title", "Clear", "--text", "C.",
                 "--attr", "ambiguous=false", "--origin", "ai", "--ground",
                 "INT-0001", "--no-interactive"]) == 0
    assert _item(graph, "REQ-0003").attrs["ambiguous"] is False
    assert _findings(graph, "REQ-0003", "ambiguous", capsys) == []
    assert _cli(["-C", graph, "ratify", "REQ-0003", "--by", "Ada"]) == 0


def test_a_true_flag_at_creation_is_stored_as_a_boolean(graph):
    assert _cli(["-C", graph, "new", "REQ", "--title", "Vague", "--text", "V.",
                 "--attr", "ambiguous=true", "--ground", "INT-0001",
                 "--no-interactive"]) == 0
    assert _item(graph, "REQ-0003").attrs["ambiguous"] is True


def test_a_value_that_is_neither_is_refused(graph, capsys):
    assert _cli(["-C", graph, "new", "REQ", "--title", "Maybe", "--text", "M.",
                 "--attr", "ambiguous=perhaps", "--ground", "INT-0001",
                 "--no-interactive"]) == 2
    assert "expected a boolean" in capsys.readouterr().err
    assert _item(graph, "REQ-0003") is None


def test_a_project_that_declares_the_attribute_keeps_its_kind(graph):
    assert _cli(["-C", graph, "schema", "attr", "add", "requirement", "ambiguous",
                 "--kind", "enum", "--values", "yes,no",
                 "--because", "reviewers record a verdict"]) == 0
    assert _cli(["-C", graph, "new", "REQ", "--title", "Verdict", "--text", "V.",
                 "--attr", "ambiguous=yes", "--ground", "INT-0001",
                 "--no-interactive"]) == 0
    assert _item(graph, "REQ-0003").attrs["ambiguous"] == "yes"
