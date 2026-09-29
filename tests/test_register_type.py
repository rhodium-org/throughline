# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A register declares the item type it holds, and `tl new` uses it (SR-0241).

`tl new` once fell back to ``requirement`` whatever the prefix, so `tl new TEST` in
a graph fresh from `tl init` made a requirement, and nothing said so until a
coverage rule scoped to requirements tripped over it (throughline#81).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from throughline.cli import main as cli_main
from throughline.storage import MANIFEST_NAME, load_project, migrate_project
from throughline.validate import validate


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _init(root: Path) -> Path:
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    return root


def _manifest(root: Path, folder: str) -> dict:
    return yaml.safe_load((root / folder / MANIFEST_NAME).read_text())


def _drop_type(root: Path, folder: str) -> None:
    path = root / folder / MANIFEST_NAME
    data = yaml.safe_load(path.read_text())
    data.pop("type", None)
    path.write_text(yaml.safe_dump(data))


def test_init_declares_each_default_registers_type(tmp_path):
    root = _init(tmp_path)
    assert {f: _manifest(root, f).get("type") for f in
            ("vision", "requirements", "nonfunctional", "non-goals", "tests")} == {
        "vision": "intent", "requirements": "requirement", "nonfunctional": "nfr",
        "non-goals": "non_goal", "tests": "test"}


def test_new_without_type_takes_the_registers_type(tmp_path):
    root = _init(tmp_path)
    for prefix, want in (("TEST", "test"), ("NG", "non_goal"), ("INT", "intent"),
                         ("NFR", "nfr"), ("REQ", "requirement")):
        assert _cli(["-C", root, "new", prefix, "--title", "t",
                     "--origin", "ai", "--no-interactive"]) == 0
        assert load_project(root).get(f"{prefix}-0001").type == want


def test_explicit_type_still_wins(tmp_path):
    root = _init(tmp_path)
    assert _cli(["-C", root, "new", "TEST", "--type", "requirement", "--title", "t",
                 "--no-interactive"]) == 0
    assert load_project(root).get("TEST-0001").type == "requirement"


def test_untyped_register_refuses_rather_than_guesses(tmp_path, capsys):
    root = _init(tmp_path)
    assert _cli(["-C", root, "register", "new", "RISK", "risks"]) == 0
    capsys.readouterr()
    assert _cli(["-C", root, "new", "RISK", "--title", "t",
                 "--no-interactive"]) != 0
    assert "declares no item type" in capsys.readouterr().err
    assert not list((root / "risks").glob("RISK-*.yml"))


def test_register_new_can_declare_a_type(tmp_path):
    root = _init(tmp_path)
    assert _cli(["-C", root, "register", "new", "RISK", "risks",
                 "--type", "risk"]) == 0
    assert _cli(["-C", root, "new", "RISK", "--title", "t",
                 "--no-interactive"]) == 0
    assert load_project(root).get("RISK-0001").type == "risk"


def test_check_names_an_item_of_another_type_than_its_register(tmp_path):
    root = _init(tmp_path)
    assert _cli(["-C", root, "new", "TEST", "--type", "requirement", "--title", "t",
                 "--no-interactive"]) == 0
    rules = {(f.rule, f.uid) for f in validate(load_project(root))}
    assert ("register-type-mismatch", "TEST-0001") in rules


def test_migrate_types_a_seeded_register_whatever_its_items_hold(tmp_path):
    """The graph #81 was found in: a TEST register from `tl init` holding an item
    the old fallback made a requirement."""
    root = _init(tmp_path)
    assert _cli(["-C", root, "new", "TEST", "--type", "requirement", "--title", "t",
                 "--no-interactive"]) == 0
    _drop_type(root, "tests")
    result = migrate_project(root)
    assert result.registers == {"TEST": "test"}
    assert _manifest(root, "tests")["type"] == "test"
    assert migrate_project(root).registers == {}


def test_migrate_types_a_custom_register_only_from_agreeing_items(tmp_path):
    root = _init(tmp_path)
    for prefix, folder in (("RISK", "risks"), ("MIX", "mixed"), ("EMP", "empty")):
        assert _cli(["-C", root, "register", "new", prefix, folder]) == 0
    for prefix, kind in (("RISK", "risk"), ("RISK", "risk"),
                         ("MIX", "risk"), ("MIX", "constraint")):
        assert _cli(["-C", root, "new", prefix, "--type", kind, "--title", "t",
                     "--no-interactive"]) == 0
    assert migrate_project(root).registers == {"RISK": "risk"}
    assert "type" not in _manifest(root, "mixed")
    assert "type" not in _manifest(root, "empty")
