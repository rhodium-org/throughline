# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""An item's normative flag follows its type, not the command that wrote it
(UR-0033; SR-0201..SR-0204).

Before this every item of every type was written `normative: true`, no command
could change it, and a graph's flags recorded which script had created each item
rather than whether the item bound anyone (issue #44). The flag feeds the
fingerprint (SR-0033) and the `unpublished` finding (SR-0096); it does not gate the
suspect cascade, and the agent brief used to say it did.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.schema import Schema, SchemaError
from throughline.storage import load_project, migrate_project
from throughline.validate import validate


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _rules(root: Path) -> set[tuple[str, str]]:
    return {(f.uid, f.rule) for f in validate(load_project(root))}


def _flag(root: Path, rel: str) -> bool:
    for line in (root / rel).read_text(encoding="utf-8").splitlines():
        if line.startswith("normative:"):
            return line.split(":", 1)[1].strip() == "true"
    raise AssertionError(f"{rel} carries no normative field")


# ------------------------------------------------- SR-0201: the type declares it

def test_a_type_absent_from_config_is_normative():
    assert Schema.from_config({}).is_normative("anything") is True
    assert Schema.from_config({"types": {"advice": {}}}).is_normative("advice") is True


def test_a_type_declares_its_items_non_normative():
    schema = Schema.from_config({"types": {"advice": {"normative": False}}})
    assert schema.is_normative("advice") is False


def test_a_non_boolean_declaration_is_a_configuration_error():
    with pytest.raises(SchemaError, match="normative must be true or false"):
        Schema.from_config({"types": {"advice": {"normative": "no"}}})


def test_tl_new_takes_the_flag_from_the_type(tmp_path):
    """The scaffold declares intent and non_goal non-normative and requirement
    normative, so one `tl new` per type shows the flag is the kind's."""
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "Why",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "R", "--ground", "INT-0001",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "NG", "--type", "non_goal", "--title", "Not",
                 "--no-interactive"]) == 0
    assert _flag(root, "vision/INT-0001.yml") is False
    assert _flag(root, "requirements/REQ-0001.yml") is True
    assert _flag(root, "non-goals/NG-0001.yml") is False


def test_schema_type_add_can_declare_a_new_type_non_normative(tmp_path):
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    assert _cli(["-C", root, "schema", "type", "add", "advice", "--non-normative",
                 "--because", "advice binds nobody"]) == 0
    cfg = tomllib.loads((root / "throughline.toml").read_text(encoding="utf-8"))
    assert cfg["types"]["advice"]["normative"] is False
    assert load_project(root).schema.is_normative("advice") is False


def test_schema_type_normative_changes_an_existing_type(tmp_path):
    """Set through the tool, on a type already declared, with the reason recorded
    beside it (SR-0184) — never by opening the file."""
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    assert _cli(["-C", root, "schema", "type", "normative", "nfr", "false",
                 "--because", "this project's NFRs are guidance"]) == 0
    text = (root / "throughline.toml").read_text(encoding="utf-8")
    assert "this project's NFRs are guidance" in text
    assert load_project(root).schema.is_normative("nfr") is False
    # And back again — the declaration is symmetric.
    assert _cli(["-C", root, "schema", "type", "normative", "nfr", "true",
                 "--because", "changed our minds"]) == 0
    assert load_project(root).schema.is_normative("nfr") is True


def test_schema_type_normative_refuses_a_no_op_and_an_undeclared_type(tmp_path, capsys):
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    assert _cli(["-C", root, "schema", "type", "normative", "intent", "false",
                 "--because", "already so"]) == 2
    assert "already declared non-normative" in capsys.readouterr().err
    assert _cli(["-C", root, "schema", "type", "normative", "ghost", "false",
                 "--because", "no such type"]) == 2
    assert "is not declared" in capsys.readouterr().err


# ------------------------------------------- SR-0202: the scaffold declares them

def test_init_declares_intent_non_goal_and_test_non_normative(tmp_path):
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    schema = load_project(root).schema
    assert schema.is_normative("intent") is False
    assert schema.is_normative("non_goal") is False
    assert schema.is_normative("test") is False
    assert schema.is_normative("requirement") is True
    assert schema.is_normative("nfr") is True


def test_the_demo_seeds_each_item_with_its_types_flag(tmp_path):
    """The seed and `tl new` read one declaration, so a fresh project holds one
    value per kind — before this the seed said false and `tl new` said true."""
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init"]) == 0
    project = load_project(root)
    for item in project.items():
        assert item.normative == project.schema.is_normative(item.type), item.uid
    assert {(f.uid, f.rule) for f in validate(project, strict=True)} == set()


# ------------------------------- SR-0203: disagreement is reported and repaired

@pytest.fixture
def drifted(tmp_path) -> Path:
    """A graph whose `requirement` type is declared non-normative after its items
    were written normative: REQ-0001 is ratified; REQ-0002 carries a stamp that
    matches REQ-0001 as it stands; REQ-0003 carries one that already disagrees."""
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "Why",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "R1", "--text", "First.",
                 "--ground", "INT-0001", "--origin", "ai", "--no-interactive"]) == 0
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    for n in ("R2", "R3"):
        assert _cli(["-C", root, "new", "REQ", "--title", n, "--text", "T.",
                     "--ground", "REQ-0001", "--no-interactive"]) == 0
        uid = "REQ-0002" if n == "R2" else "REQ-0003"
        assert _cli(["-C", root, "link", uid, "REQ-0001", "--type", "derives_from",
                     "--stamp"]) == 0
    # REQ-0003's stamp is taken before a wording change, so it already disagrees.
    assert _cli(["-C", root, "amend", "REQ-0001", "--text", "First, reworded."]) == 0
    assert _cli(["-C", root, "link", "REQ-0002", "REQ-0001", "--type", "derives_from",
                 "--stamp"]) == 0
    assert _cli(["-C", root, "schema", "type", "normative", "requirement", "false",
                 "--because", "these are guidance"]) == 0
    return root


def _stamp(root: Path, uid: str) -> str:
    item = load_project(root).get(uid)
    return next(l.stamp for l in item.links if l.target == "REQ-0001")


def test_check_reports_an_item_disagreeing_with_its_type(drifted):
    rules = _rules(drifted)
    assert ("REQ-0001", "normative-mismatch") in rules
    assert ("REQ-0002", "normative-mismatch") in rules
    assert ("INT-0001", "normative-mismatch") not in rules
    finding = next(f for f in validate(load_project(drifted))
                   if f.rule == "normative-mismatch")
    assert finding.severity == "warning"
    assert "tl migrate" in finding.message


def test_migrate_rewrites_the_flag_and_names_every_item(drifted, capsys):
    stale_before = _stamp(drifted, "REQ-0003")
    result = migrate_project(drifted)
    assert result.normative == {"REQ-0001": False, "REQ-0002": False,
                                "REQ-0003": False}
    for uid in ("REQ-0001", "REQ-0002", "REQ-0003"):
        assert _flag(drifted, f"requirements/{uid}.yml") is False
    rules = _rules(drifted)
    assert not {r for r in rules if r[1] == "normative-mismatch"}
    # The stamp that matched was refreshed; the one that disagreed is untouched.
    assert result.restamped == [("REQ-0002", "REQ-0001")]
    assert ("REQ-0002", "suspect-link") not in rules
    assert ("REQ-0003", "suspect-link") in rules
    assert _stamp(drifted, "REQ-0003") == stale_before
    # The record was not written: the ratified item is stale, for a person.
    assert result.stale == ["REQ-0001"]
    assert ("REQ-0001", "ratified-stale") in rules
    item = load_project(drifted).get("REQ-0001")
    assert item.attrs["ratified_by"] == "Ada"
    assert "ratified_backfilled" not in item.attrs
    # And the command says all of it.
    assert _cli(["-C", drifted, "migrate"]) == 0
    out = capsys.readouterr().out
    assert "nothing to migrate" in out          # the second run is idempotent


def test_migrate_output_names_the_rewrites_and_the_stale_records(drifted, capsys):
    assert _cli(["-C", drifted, "migrate"]) == 0
    out = capsys.readouterr().out
    assert "rewrote the normative flag on 3 item(s)" in out
    assert "  REQ-0001 = false" in out
    assert "refreshed 1 link stamp(s)" in out
    assert "1 ratified item(s) now await re-ratification" in out
    assert "REQ-0001" in out.split("await re-ratification")[1]


def test_migrate_is_a_no_op_on_a_graph_that_agrees_with_its_types(tmp_path, capsys):
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init"]) == 0
    result = migrate_project(root)
    assert result.normative == {} and result.restamped == [] and result.stale == []
    assert _cli(["-C", root, "migrate"]) == 0
    assert "nothing to migrate" in capsys.readouterr().out


# -------------------------------- SR-0204: the brief says what the flag does

def test_the_brief_describes_the_flag_by_its_effects(tmp_path, capsys):
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    assert _cli(["-C", root, "context"]) == 0
    out = capsys.readouterr().out
    assert "mark dependents suspect" not in out
    line = next(l for l in out.splitlines() if l.startswith("normative: true"))
    assert "fingerprint" in line
    assert "published document" in out.split("normative: true", 1)[1].split("\n", 2)[1]
    assert "### `intent` _(root, delivery-root, non-normative)_" in out
    assert "### `non_goal` _(root, non-normative)_" in out
    assert "### `requirement`" in out and "`requirement` _(" not in out
