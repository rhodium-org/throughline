# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A composed check reports what loading found (SR-0240).

The union was built as a new project that copied only the registers, so the prefix
clashes, same-folder UID repeats and malformed links the loader records on a project
never reached the validator: every composing project's check passed a graph that a
standalone check of the same files fails. The seam lists those rules as reportable
on borrowed items, but nothing produced them.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from throughline.cli import main as tl_main
from throughline.model import Item, Link, Project, Register
from throughline.union import build_union, translate_finding
from throughline.validate import Finding


def _check(consumer: Path, capsys, *extra: str) -> tuple[int, str, str]:
    capsys.readouterr()
    rc = tl_main(["-C", str(consumer), "check", "--base", "", *extra])
    out = capsys.readouterr()
    return rc, out.out, out.err


def _second_register(root: Path, prefix: str) -> Path:
    clash = root / "more-requirements"
    clash.mkdir()
    (clash / ".register.yml").write_text(f"prefix: {prefix}\ndigits: 4\n",
                                         encoding="utf-8")
    return clash


# ------------------------------------------------------------- in the consumer

def test_a_composed_project_with_a_prefix_collision_fails_check(consumer_dir, capsys):
    clash = _second_register(consumer_dir, "SR")
    rc, out, err = _check(consumer_dir, capsys)
    assert rc == 1
    assert "prefix-collision" in out
    assert "prefix 'SR' is declared by 2 registers" in out
    assert str(clash) in out
    assert "composed graph is sound" not in err


def test_the_prefix_collision_is_an_error_in_json_too(consumer_dir, capsys):
    _second_register(consumer_dir, "SR")
    rc, out, _err = _check(consumer_dir, capsys, "--format", "json")
    assert rc == 1
    found = [f for f in json.loads(out) if f["rule"] == "prefix-collision"]
    assert [(f["uid"], f["severity"]) for f in found] == [("SR", "error")]


def test_a_uid_repeated_in_one_consumer_folder_fails_a_composed_check(consumer_dir,
                                                                     capsys):
    folder = consumer_dir / "system-requirements"
    shutil.copy(folder / "SR-0001.yml", folder / "SR-0001-merged.yml")
    rc, out, _err = _check(consumer_dir, capsys)
    assert rc == 1
    assert any("SR-0001" in line and "uid-collision" in line
               for line in out.splitlines())


def test_a_malformed_link_in_the_consumer_fails_a_composed_check(consumer_dir, capsys):
    (consumer_dir / "system-requirements" / "SR-0002.yml").write_text(
        "uid: SR-0002\ntype: system_requirement\nstatus: approved\ntitle: t\n"
        "text: t.\nlinks:\n- target: INT-0001\n  type: derives_from\n"
        "- type: relates\n", encoding="utf-8")
    rc, out, _err = _check(consumer_dir, capsys)
    assert rc == 1
    assert any("SR-0002" in line and "malformed-link" in line
               for line in out.splitlines())


# ------------------------------------------------ in a source, in its own words

def test_a_prefix_collision_in_a_source_is_named_namespace_prefix(consumer_dir,
                                                                 source_dir, capsys):
    _second_register(source_dir, "SR")
    rc, out, _err = _check(consumer_dir, capsys)
    assert rc == 1
    assert "prefix 'toy:SR' is declared by 2 registers" in out
    assert "TOYSR" not in out


def test_a_uid_repeated_in_a_source_folder_is_named_namespace_uid(consumer_dir,
                                                                 source_dir, capsys):
    folder = source_dir / "system-requirements"
    shutil.copy(folder / "SR-0001.yml", folder / "SR-0001-merged.yml")
    rc, out, _err = _check(consumer_dir, capsys)
    assert rc == 1
    assert any("toy:SR-0001" in line and "uid-collision" in line
               for line in out.splitlines())
    assert "TOYSR" not in out


def test_a_malformed_link_in_a_source_is_named_namespace_uid(consumer_dir, source_dir,
                                                            capsys):
    item = source_dir / "system-requirements" / "SR-0001.yml"
    item.write_text(item.read_text(encoding="utf-8").replace(
        "  type: implements\n", "  type: implements\n- type: relates\n"),
        encoding="utf-8")
    rc, out, _err = _check(consumer_dir, capsys)
    assert rc == 1
    assert any("toy:SR-0001" in line and "malformed-link" in line
               for line in out.splitlines())


# ------------------------------------------------------------------ the union

def _loaded(prefix: str, uid: str) -> Project:
    p = Project(path=Path("."), config={"project": {"name": "p"}})
    item = Item(uid=uid, type="t", status="approved",
                links=[Link(target="X-0001", type="relates")])
    p.registers[prefix] = Register(prefix=prefix, items={uid: item})
    p.prefix_conflicts = {prefix: ["a", "b"]}
    p.duplicate_uids = {uid}
    p.load_errors = [(uid, "f.yml", "link entry #2 missing required key 'target'")]
    p.nested_projects = [Path(prefix.lower())]
    return p


def test_the_union_carries_what_loading_recorded():
    union = build_union(_loaded("SR", "SR-0001"), {"toy": _loaded("UR", "UR-0001")})
    assert union.project.prefix_conflicts == {"SR": ["a", "b"], "TOYUR": ["a", "b"]}
    assert union.project.duplicate_uids == {"SR-0001", "TOYUR-0001"}
    assert sorted(e[0] for e in union.project.load_errors) == ["SR-0001", "TOYUR-0001"]
    assert union.qualified("TOYUR") == "toy:UR"
    assert union.qualified("TOYUR-0001") == "toy:UR-0001"
    # The union stands where the consumer stands, so only the consumer's own tree
    # has anything to say about projects nested in it.
    assert union.project.nested_projects == [Path("sr")]


def test_translation_reads_back_a_prefix_and_leaves_a_longer_token_alone():
    union = build_union(_loaded("SR", "SR-0001"), {"toy": _loaded("UR", "UR-0001")})
    finding = Finding("prefix-collision", "error", "TOYUR", "b",
                      "prefix 'TOYUR' is declared by 2 registers; not XTOYUR-0001")
    shown = translate_finding(finding, union, union.pattern())
    assert shown.uid == "toy:UR"
    assert shown.message == ("prefix 'toy:UR' is declared by 2 registers; "
                             "not XTOYUR-0001")
