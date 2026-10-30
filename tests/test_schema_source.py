# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A project takes its schema from one declared source and declares only what it
adds (UR-0041, SR-0275 to SR-0279).

Six presentation series each held a hand copy of the item types their
application reads, and drifted to five schemas across two format versions. These
tests hold the remedy to its requirements: the schema a follower is checked by is
the source's plus its own additions (SR-0275); a follower may add but never
redefine (SR-0276); migration removes the copy it no longer needs (SR-0277);
every operation that reads the schema resolves that source and no other
(SR-0278); and what the Tool reports says where each declaration came from, while
`tl schema` refuses to change one the source owns (SR-0279).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.storage import ProjectError, load_project, migrate_project
from throughline.validate import validate

STANDARD = """
[types.slide]
attrs.layout = { type = "enum", values = ["cards", "stats"], required = true }
"""


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _init(root: Path) -> Path:
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    return root


def _append(root: Path, text: str) -> None:
    cfg = root / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + text, encoding="utf-8")


def _standard(tmp_path: Path, name: str = "std", extra: str = STANDARD) -> Path:
    std = _init(tmp_path / name)
    _append(std, extra)
    return std


def _follower(tmp_path: Path, extra: str = "", *, source: str = "../std") -> Path:
    """A graph that names ``source`` as its schema source and keeps the copy of the
    schema `tl init` wrote, as every series graph does today."""
    fol = _init(tmp_path / "fol")
    _append(fol, f'\n# the standard\n[[sources]]\nnamespace = "std"\npath = "{source}"\n'
                 f"schema = true\n{extra}")
    return fol


def _slide_register(root: Path) -> None:
    assert _cli(["-C", root, "register", "new", "SLD", "slides", "--type", "slide"]) == 0


# --------------------------------------------------------------------------- #
# SR-0275 — the schema is the source's, with the project's own declarations added
# --------------------------------------------------------------------------- #

def test_a_type_the_follower_never_declares_is_checked_by_the_standard(tmp_path):
    _standard(tmp_path)
    fol = _follower(tmp_path)
    _slide_register(fol)
    assert _cli(["-C", fol, "new", "SLD", "--title", "cover", "--attr", "layout=cards",
                 "--no-interactive"]) == 0
    assert not [f for f in validate(load_project(fol)) if f.rule in ("schema", "unknown-key")]
    # The standard's enum binds the follower's items.
    assert _cli(["-C", fol, "new", "SLD", "--title", "x", "--attr", "layout=video",
                 "--no-interactive"]) != 0
    project = load_project(fol)
    assert project.schema.attr("slide", "layout").values == ("cards", "stats")
    assert "slide" not in (project.config.get("types") or {})


def test_a_project_without_a_schema_source_is_unchanged(tmp_path):
    root = _init(tmp_path / "plain")
    project = load_project(root)
    assert project.schema_config is None and project.inheritance is None
    assert project.formed_config is project.config


def test_a_source_named_only_for_its_items_lends_no_schema(tmp_path):
    _standard(tmp_path)
    fol = _init(tmp_path / "fol")
    _append(fol, '\n[[sources]]\nnamespace = "std"\npath = "../std"\n')
    assert load_project(fol).schema.attr("slide", "layout") is None


def test_at_most_one_source_is_the_schema_source(tmp_path):
    _standard(tmp_path)
    _standard(tmp_path, "other")
    fol = _follower(tmp_path, '\n[[sources]]\nnamespace = "other"\npath = "../other"\n'
                              "schema = true\n")
    with pytest.raises(ProjectError, match="both marked schema = true"):
        load_project(fol)


def test_schema_must_be_true_or_false(tmp_path):
    _standard(tmp_path)
    fol = _init(tmp_path / "fol")
    _append(fol, '\n[[sources]]\nnamespace = "std"\npath = "../std"\nschema = "yes"\n')
    with pytest.raises(ProjectError, match="not true or false"):
        load_project(fol)


def test_a_schema_source_is_formed_the_same_way_to_any_depth(tmp_path):
    _standard(tmp_path, "base", "\n[types.deck]\nattrs.audience = { type = \"text\" }\n")
    mid = _standard(tmp_path, "std", STANDARD)
    _append(mid, '\n[[sources]]\nnamespace = "base"\npath = "../base"\nschema = true\n')
    fol = _follower(tmp_path)
    schema = load_project(fol).schema
    assert schema.attr("deck", "audience") is not None
    assert schema.attr("slide", "layout") is not None


def test_a_cycle_of_schema_sources_is_refused(tmp_path):
    std = _standard(tmp_path)
    _follower(tmp_path)
    _append(std, '\n[[sources]]\nnamespace = "fol"\npath = "../fol"\nschema = true\n')
    with pytest.raises(ProjectError, match="schema sources form a cycle"):
        load_project(tmp_path / "fol")


# --------------------------------------------------------------------------- #
# SR-0276 — a follower may add to its schema source but never redefine it
# --------------------------------------------------------------------------- #

def test_an_identical_copy_is_accepted(tmp_path):
    _standard(tmp_path)
    fol = _follower(tmp_path, STANDARD)
    assert load_project(fol).schema.attr("slide", "layout").required


def test_widening_an_inherited_enum_is_refused_naming_both_values(tmp_path, capsys):
    _standard(tmp_path)
    fol = _follower(tmp_path, '\n[types.slide]\nattrs.layout = { type = "enum", '
                              'values = ["cards", "stats", "video"], required = true }\n')
    assert _cli(["-C", fol, "check"]) != 0
    err = capsys.readouterr().err
    assert "item type 'slide' attribute 'layout'" in err and "SR-0276" in err
    assert "'video'" in err and "schema source 'std'" in err


def test_saying_anything_else_about_an_inherited_type_is_refused(tmp_path):
    _standard(tmp_path)
    fol = _follower(tmp_path, "\n[types.slide]\nnormative = false\n")
    with pytest.raises(ProjectError, match="item type 'slide' normative"):
        load_project(fol)


def test_a_follower_adds_an_attribute_a_status_and_a_link_type(tmp_path):
    _standard(tmp_path)
    fol = _follower(tmp_path, '\n[types.slide]\nattrs.tone = { type = "string" }\n')
    cfg = (fol / "throughline.toml").read_text(encoding="utf-8")
    cfg = cfg.replace('"rejected", "deleted"]', '"rejected", "deleted", "parked"]', 1)
    cfg = cfg.replace('"mitigates", "assumes"]', '"mitigates", "assumes", "shows"]', 1)
    (fol / "throughline.toml").write_text(cfg, encoding="utf-8")
    schema = load_project(fol).schema
    assert schema.attr("slide", "tone") is not None
    assert schema.attr("slide", "layout") is not None
    assert "parked" in schema.statuses and "shows" in schema.link_types


# --------------------------------------------------------------------------- #
# SR-0277 — migration removes declarations the schema source already makes
# --------------------------------------------------------------------------- #

def test_migrate_removes_the_copy_keeps_what_is_added_and_runs_once(tmp_path, capsys):
    _standard(tmp_path)
    fol = _follower(tmp_path, '\n# the slide type, copied\n[types.slide]\n'
                              'attrs.layout = { type = "enum", values = ["cards", "stats"], '
                              'required = true }\n# a local attribute\n'
                              'attrs.tone = { type = "string" }\n')
    before = load_project(fol).schema
    assert _cli(["-C", fol, "migrate"]) == 0
    out = capsys.readouterr().out
    assert "already makes identically" in out
    assert "item type 'slide' attribute 'layout'" in out and "status 'draft'" in out
    text = (fol / "throughline.toml").read_text(encoding="utf-8")
    for gone in ("[transitions]", "[status.roles]", "[links]", "[grounding]",
                 "attrs.layout", "[types.requirement]"):
        assert gone not in text, gone
    # What the project adds, and the comments on it, stay.
    assert "attrs.tone" in text and "# a local attribute" in text
    assert "# the slide type, copied" in text
    # The comment introducing the coverage rule belongs to it, not to the
    # emptied table before it.
    assert "# Coverage: every live requirement" in text and "[[rules.coverage]]" in text
    after = load_project(fol).schema
    assert after == before
    assert _cli(["-C", fol, "check"]) == 0
    assert migrate_project(fol).inherited == ()


def test_migrate_runs_no_configuration_backfill_for_a_follower(tmp_path):
    _standard(tmp_path)
    fol = _init(tmp_path / "fol")
    cfg = fol / "throughline.toml"
    # A follower that never had the template's tables: the source supplies them,
    # so migration must not write a status vocabulary or roles here.
    cfg.write_text('[project]\nname = "fol"\nformat_version = 4\n\n[[sources]]\n'
                   'namespace = "std"\npath = "../std"\nschema = true\n',
                   encoding="utf-8")
    migrate_project(fol)
    text = cfg.read_text(encoding="utf-8")
    assert "[status" not in text and "[links]" not in text
    assert load_project(fol).schema.status_roles["initial"] == "draft"


# --------------------------------------------------------------------------- #
# SR-0278 — every operation that reads the schema resolves the schema source
# --------------------------------------------------------------------------- #

def test_an_amend_resolves_the_schema_source_and_no_other(tmp_path, capsys):
    _standard(tmp_path)
    fol = _follower(tmp_path, '\n[[sources]]\nnamespace = "gone"\npath = "../gone"\n')
    _slide_register(fol)
    assert _cli(["-C", fol, "new", "SLD", "--title", "cover", "--attr", "layout=cards",
                 "--no-interactive"]) == 0
    # The local operation needs only the schema source, so the missing one is
    # never read; the union operation needs it and says so.
    assert _cli(["-C", fol, "amend", "SLD-0001", "--title", "the cover"]) == 0
    capsys.readouterr()
    assert _cli(["-C", fol, "check"]) != 0
    assert "gone" in capsys.readouterr().err


def test_a_cold_cache_for_a_schema_source_is_refused_in_cache_only_mode(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TL_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("TL_OFFLINE", "1")
    fol = _init(tmp_path / "fol")
    _append(fol, '\n[[sources]]\nnamespace = "std"\nschema = true\n'
                 'url = "https://example.invalid/std.git"\nref = "v1"\n')
    assert _cli(["-C", fol, "amend", "INT-0001", "--title", "x"]) != 0
    err = capsys.readouterr().err
    assert "schema source 'std'" in err


# --------------------------------------------------------------------------- #
# SR-0279 — the reported schema says where each declaration came from
# --------------------------------------------------------------------------- #

def test_the_brief_marks_what_the_source_declares(tmp_path, capsys):
    _standard(tmp_path)
    fol = _follower(tmp_path, '\n[types.slide]\nattrs.tone = { type = "string" }\n')
    assert _cli(["-C", fol, "context"]) == 0
    out = capsys.readouterr().out
    assert "## Schema source" in out
    assert "### `slide` _(from `std`)_" in out
    assert "| `layout` (from `std`) |" in out and "| `tone` |" in out


def test_the_brief_of_a_plain_project_says_nothing_of_a_source(tmp_path, capsys):
    root = _init(tmp_path / "plain")
    assert _cli(["-C", root, "context"]) == 0
    out = capsys.readouterr().out
    assert "Schema source" not in out and "(from `" not in out


def test_the_dump_carries_the_formed_schema_and_what_was_inherited(tmp_path, capsys):
    _standard(tmp_path)
    fol = _follower(tmp_path)
    capsys.readouterr()
    assert _cli(["-C", fol, "dump"]) == 0
    dump = json.loads(capsys.readouterr().out)
    block = dump["schema_source"]
    assert block["namespace"] == "std"
    assert ["attr", "slide", "layout"] in block["inherited"]
    assert "slide" in block["formed"]["types"]
    assert "slide" not in (dump["config"].get("types") or {})


def test_schema_show_marks_each_declaration(tmp_path, capsys):
    _standard(tmp_path)
    fol = _follower(tmp_path, '\n[types.slide]\nattrs.tone = { type = "string" }\n')
    capsys.readouterr()
    assert _cli(["-C", fol, "schema", "show", "--format", "json"]) == 0
    rows = {r["declaration"]: r["from"] for r in json.loads(capsys.readouterr().out)}
    assert rows["item type 'slide' attribute 'layout'"] == "std"
    assert rows["item type 'slide' attribute 'tone'"] is None


def test_a_schema_operation_on_an_inherited_declaration_is_refused_unwritten(
        tmp_path, capsys):
    _standard(tmp_path)
    fol = _follower(tmp_path)
    cfg = fol / "throughline.toml"
    before = cfg.read_text(encoding="utf-8")
    for argv in (["status", "remove", "draft"], ["type", "remove", "slide"],
                 ["attr", "add", "slide", "layout", "--kind", "string"],
                 ["transition", "deny", "draft", "deleted"]):
        capsys.readouterr()
        assert _cli(["-C", fol, "schema", *argv, "--because", "x"]) != 0, argv
        err = capsys.readouterr().err
        assert "schema source 'std'" in err and "SR-0279" in err, argv
        assert cfg.read_text(encoding="utf-8") == before, argv


def test_an_attribute_is_added_to_an_inherited_type_in_the_project_itself(tmp_path):
    _standard(tmp_path)
    fol = _follower(tmp_path)
    assert _cli(["-C", fol, "schema", "attr", "add", "slide", "caption",
                 "--kind", "string", "--because", "slide captions"]) == 0
    project = load_project(fol)
    assert project.config["types"]["slide"]["attrs"] == {"caption": {"type": "string"}}
    assert project.schema.attr("slide", "layout") is not None
