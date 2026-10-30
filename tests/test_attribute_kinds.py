# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A declared attribute holds a value of its declared kind (SR-0282).

Check validated only required attributes and enum membership, and a date was
validated nowhere: a source's checked day reading "next tuesday" passed every
gate. These tests hold check and the writes to the kinds the schema declares, and
leave an undeclared attribute free-form.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.schema import kind_accepts
from throughline.storage import load_project, write_item
from throughline.validate import validate

KINDS = """attrs.count = { type = "int" }
attrs.weight = { type = "float" }
attrs.flag = { type = "bool" }
attrs.checked = { type = "date" }
attrs.label = { type = "string" }
attrs.notes = { type = "text" }
attrs.loose = {}
"""


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "p"
    assert _cli(["-C", root, "init", "--name", "t", "--bare"]) == 0
    cfg = root / "throughline.toml"
    text = cfg.read_text(encoding="utf-8")
    assert "[types.requirement]\n" in text
    cfg.write_text(text.replace("[types.requirement]\n", "[types.requirement]\n" + KINDS, 1),
                   encoding="utf-8")
    assert _cli(["-C", root, "register", "new", "INT", "vision"]) == 0
    assert _cli(["-C", root, "register", "new", "FR", "features"]) == 0
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "why"]) == 0
    assert _cli(["-C", root, "new", "FR", "--type", "requirement", "--title", "r",
                 "--ground", "INT-0001"]) == 0
    return root


def _hold(root: Path, **attrs) -> list:
    project = load_project(root)
    item = project.get("FR-0001")
    item.attrs.update(attrs)
    write_item(item, project.register_of("FR-0001"))
    return [f for f in validate(load_project(root)) if f.rule == "attribute-kind"]


@pytest.mark.parametrize("kind,good,bad", [
    ("int", [3, 0, -2], [True, 2.5, "3"]),
    ("float", [2.5, 3], [True, "2.5"]),
    ("bool", [True, False], [1, "yes"]),
    ("date", [dt.date(2026, 10, 30), "2026-10-30"],
     ["next tuesday", "2026-13-01", "30/10/2026", dt.datetime(2026, 10, 30, 9)]),
    ("string", ["x", ""], [3, True]),
    ("text", ["a\nb"], [3]),
])
def test_each_kind_accepts_its_values_and_no_other(kind, good, bad):
    assert all(kind_accepts(kind, v) for v in good)
    assert not any(kind_accepts(kind, v) for v in bad)


def test_no_kind_and_enum_accept_anything_here():
    assert kind_accepts(None, object())
    assert kind_accepts("enum", "whatever")  # membership is its own check (SR-0023)


def test_check_reports_a_value_of_the_wrong_kind(tmp_path):
    root = _project(tmp_path)
    found = _hold(root, checked="next tuesday", count="three", flag="yes")
    assert sorted(f.message.split("'")[1] for f in found) == ["checked", "count", "flag"]
    assert all(f.uid == "FR-0001" and f.severity == "error" for f in found)
    assert any("'next tuesday'" in f.message and "date" in f.message for f in found)


def test_values_of_the_right_kind_and_free_form_ones_pass(tmp_path):
    root = _project(tmp_path)
    assert _hold(root, count=3, weight=1.5, flag=True, checked="2026-10-30",
                 label="x", notes="y", loose=["anything", 1], undeclared={"a": 1}) == []


def test_the_rule_can_be_lowered_alone(tmp_path):
    root = _project(tmp_path)
    cfg = root / "throughline.toml"
    text = cfg.read_text(encoding="utf-8")
    if "\n[rules]\n" in text:
        text = text.replace("\n[rules]\n", '\n[rules]\nattribute-kind = "warning"\n', 1)
    else:
        text = text.replace("[types.requirement]", '[rules]\nattribute-kind = "warning"\n\n'
                            "[types.requirement]", 1)
    cfg.write_text(text, encoding="utf-8")
    found = _hold(root, checked="soon")
    assert [f.severity for f in found] == ["warning"]


def test_writes_refuse_a_date_that_is_not_one(tmp_path, capsys):
    root = _project(tmp_path)
    assert _cli(["-C", root, "new", "FR", "--type", "requirement", "--title", "x",
                 "--ground", "INT-0001", "--attr", "checked=next tuesday"]) != 0
    assert "YYYY-MM-DD" in capsys.readouterr().err
    assert _cli(["-C", root, "amend", "FR-0001", "--attr", "checked=2026-02-30"]) != 0
    assert _cli(["-C", root, "amend", "FR-0001", "--attr", "checked=2026-10-30"]) == 0
    assert load_project(root).get("FR-0001").attrs["checked"] == "2026-10-30"
