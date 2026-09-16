# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Removing an attribute through the tool (SR-0206), withdrawing one together with
its values (SR-0207), and the origin that only ratification may take out of the
machine-origin set (SR-0208).

Before this, `tl schema attr remove` left the withdrawn value on every item of the
type, `tl amend` refused to clear it because the type no longer declared it, and
published documents went on rendering it (issue #37). Relabelling a machine-authored
item's origin also removed its unratified finding while it stayed proposed.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.storage import load_project
from throughline.validate import validate


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _attrs(root: Path, uid: str) -> dict:
    return dict(load_project(root).get(uid).attrs)


def _rules(root: Path) -> set[tuple[str, str]]:
    return {(f.uid, f.rule) for f in validate(load_project(root))}


@pytest.fixture
def graph(tmp_path) -> Path:
    """A default project: an intent, a machine-authored requirement carrying a
    normative priority, and a mitigation type whose `letter` attribute is about to
    be withdrawn from two items."""
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "Why",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "R", "--text", "T.",
                 "--origin", "ai", "--attr", "priority=must", "--ground", "INT-0001",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "register", "new", "MIT", "mitigations"]) == 0
    assert _cli(["-C", root, "schema", "type", "add", "mitigation",
                 "--because", "t"]) == 0
    assert _cli(["-C", root, "schema", "attr", "add", "mitigation", "letter",
                 "--kind", "enum", "--values", "S,T", "--required",
                 "--because", "t"]) == 0
    for letter in ("S", "T"):
        assert _cli(["-C", root, "new", "MIT", "--type", "mitigation", "--title", letter,
                     "--origin", "human", "--attr", f"letter={letter}",
                     "--ground", "INT-0001", "--no-interactive"]) == 0
    return root


# ----------------------------------------- SR-0206: amend removes an attribute

def test_amend_unset_clears_an_attribute_the_type_no_longer_declares(graph, capsys):
    assert _cli(["-C", graph, "schema", "attr", "remove", "mitigation", "letter",
                 "--because", "withdrawn"]) == 0
    capsys.readouterr()
    assert _cli(["-C", graph, "amend", "MIT-0001", "--unset", "letter"]) == 0
    out = capsys.readouterr().out
    assert "amended MIT-0001 — letter (unset)" in out
    assert "normative content unchanged" in out
    assert "letter" not in _attrs(graph, "MIT-0001")
    assert _attrs(graph, "MIT-0002")["letter"] == "T"          # only the one named


def test_removing_a_normative_attribute_is_a_content_change(graph, capsys):
    assert _cli(["-C", graph, "new", "REQ", "--title", "D", "--text", "d.",
                 "--ground", "REQ-0001", "--no-interactive"]) == 0
    assert _cli(["-C", graph, "link", "REQ-0002", "REQ-0001", "--type", "derives_from",
                 "--stamp"]) == 0
    capsys.readouterr()
    assert _cli(["-C", graph, "amend", "REQ-0001", "--unset", "priority"]) == 0
    assert "1 dependent item(s) now suspect: REQ-0002" in capsys.readouterr().out


@pytest.mark.parametrize("setup, uid, name, says", [
    ([], "MIT-0001", "colour", "carries no attribute 'colour'"),
    ([], "MIT-0001", "letter", "declares 'letter' as required"),
    ([["ratify", "REQ-0001", "--by", "Ada"]], "REQ-0001", "ratified_by",
     "part of the ratification record"),
    ([["new", "REQ", "--title", "A", "--attr", "ambiguous=true", "--ground",
       "INT-0001", "--no-interactive"]], "REQ-0002", "ambiguous",
     "removing the flag does not clarify it"),
    ([], "REQ-0001", "origin", "is machine-authored (origin 'ai')"),
])
def test_amend_unset_refuses_what_a_gate_reads(graph, capsys, setup, uid, name, says):
    for argv in setup:
        assert _cli(["-C", graph, *argv]) == 0
    before = next(graph.rglob(f"{uid}.yml")).read_bytes()
    capsys.readouterr()
    assert _cli(["-C", graph, "amend", uid, "--unset", name]) == 2
    assert says in capsys.readouterr().err
    assert next(graph.rglob(f"{uid}.yml")).read_bytes() == before


def test_attr_and_unset_naming_one_key_is_refused(graph, capsys):
    assert _cli(["-C", graph, "amend", "REQ-0001", "--attr", "priority=should",
                 "--unset", "priority"]) == 2
    assert "both name priority" in capsys.readouterr().err
    assert _attrs(graph, "REQ-0001")["priority"] == "must"


# ------------------------- SR-0207: withdrawing names, and can clear, the carriers

def test_withdrawing_names_the_carriers_and_leaves_them(graph, capsys):
    assert _cli(["-C", graph, "schema", "attr", "remove", "mitigation", "letter",
                 "--because", "withdrawn"]) == 0
    out = capsys.readouterr().out
    assert "2 item(s) still carry 'letter': MIT-0001 MIT-0002" in out
    assert "tl schema attr remove mitigation letter --unset" in out
    assert _attrs(graph, "MIT-0001")["letter"] == "S"


def test_withdrawing_with_unset_clears_the_values_in_the_same_run(graph, capsys):
    assert _cli(["-C", graph, "schema", "attr", "remove", "mitigation", "letter",
                 "--unset", "--because", "one item needs two mitigates links"]) == 0
    assert "removed 'letter' from 2 item(s): MIT-0001 MIT-0002" in capsys.readouterr().out
    cfg = tomllib.loads((graph / "throughline.toml").read_text(encoding="utf-8"))
    assert "letter" not in (cfg["types"]["mitigation"].get("attrs") or {})
    assert "letter" not in _attrs(graph, "MIT-0001")
    assert "letter" not in _attrs(graph, "MIT-0002")


def test_unset_finishes_a_withdrawal_that_left_values_behind(graph, capsys):
    """The repair for a graph withdrawn on an earlier release: no value in the
    configuration changes, and the reason is recorded beside the type."""
    assert _cli(["-C", graph, "schema", "attr", "remove", "mitigation", "letter",
                 "--because", "withdrawn"]) == 0
    cfg_file = graph / "throughline.toml"
    before = tomllib.loads(cfg_file.read_text(encoding="utf-8"))
    assert _cli(["-C", graph, "schema", "attr", "remove", "mitigation", "letter",
                 "--because", "clear what the old withdrawal left"]) == 2
    assert "pass --unset to remove it from them" in capsys.readouterr().err
    assert _cli(["-C", graph, "schema", "attr", "remove", "mitigation", "letter",
                 "--unset", "--because", "clear what the old withdrawal left"]) == 0
    assert "removed 'letter' from 2 item(s)" in capsys.readouterr().out
    text = cfg_file.read_text(encoding="utf-8")
    assert tomllib.loads(text) == before
    assert "clear what the old withdrawal left" in text
    assert "letter" not in _attrs(graph, "MIT-0002")
    # Nothing left to finish: the refusal is the one it always was.
    assert _cli(["-C", graph, "schema", "attr", "remove", "mitigation", "letter",
                 "--unset", "--because", "again"]) == 2
    assert "declares no attribute 'letter'" in capsys.readouterr().err


def test_withdrawing_and_declaring_again_keeps_the_values(graph):
    """Backward compatibility: the only way to change a declaration still works."""
    assert _cli(["-C", graph, "schema", "attr", "remove", "mitigation", "letter",
                 "--because", "widen"]) == 0
    assert _cli(["-C", graph, "schema", "attr", "add", "mitigation", "letter",
                 "--kind", "enum", "--values", "S,T,U", "--because", "widen"]) == 0
    assert _attrs(graph, "MIT-0001")["letter"] == "S"
    assert _attrs(graph, "MIT-0002")["letter"] == "T"


def test_unset_across_a_type_refuses_a_machine_origin_and_writes_nothing(graph, capsys):
    cfg_file = graph / "throughline.toml"
    cfg_before = cfg_file.read_bytes()
    item_before = next(graph.rglob("REQ-0001.yml")).read_bytes()
    assert _cli(["-C", graph, "schema", "attr", "remove", "requirement", "origin",
                 "--unset", "--because", "provenance is noise"]) == 2
    err = capsys.readouterr().err
    assert "REQ-0001 is machine-authored" in err and "nothing was changed" in err
    assert cfg_file.read_bytes() == cfg_before
    assert next(graph.rglob("REQ-0001.yml")).read_bytes() == item_before


# ------------------------ SR-0208: only ratification takes an origin out of the set

def test_amend_cannot_relabel_a_machine_authored_item(graph, capsys):
    assert ("REQ-0001", "unratified") in _rules(graph)
    assert _cli(["-C", graph, "amend", "REQ-0001", "--attr", "origin=human"]) == 2
    assert "accepted by `tl ratify`, not by relabelling it" in capsys.readouterr().err
    assert _attrs(graph, "REQ-0001")["origin"] == "ai"
    assert ("REQ-0001", "unratified") in _rules(graph)


def test_the_refusal_stands_after_ratification(graph, capsys):
    assert _cli(["-C", graph, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    assert _cli(["-C", graph, "amend", "REQ-0001", "--attr", "origin=human"]) == 2


def test_an_origin_may_move_within_the_set_or_into_it(graph):
    assert _cli(["-C", graph, "amend", "REQ-0001", "--attr", "origin=hybrid"]) == 0
    assert _cli(["-C", graph, "new", "REQ", "--title", "H", "--origin", "human",
                 "--ground", "INT-0001", "--no-interactive"]) == 0
    assert _cli(["-C", graph, "amend", "REQ-0002", "--attr", "origin=ai"]) == 0
    assert _attrs(graph, "REQ-0001")["origin"] == "hybrid"
    assert _attrs(graph, "REQ-0002")["origin"] == "ai"
