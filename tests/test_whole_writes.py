# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A change is written whole or not at all (SR-0355, SR-0356, SR-0304).

Each test makes a change fail part-way, by a refusal the Tool gives or by a file
that cannot be written, and compares every file of the project with what stood
before the command ran.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

import throughline.atomic as atomic
from throughline.atomic import Snapshot, write_all
from throughline.cli import main as cli_main
from throughline.storage import ProjectError, migrate_project


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts}


def _demo(root: Path) -> Path:
    assert _cli(["-C", root, "init"]) == 0
    for title in ("Two", "Three"):
        assert _cli(["-C", root, "new", "REQ", "--title", title, "--text", "t.",
                     "--ground", "INT-0001", "--no-interactive"]) == 0
    return root


def _fail_the(n: int, monkeypatch) -> None:
    """Make the ``n``-th move of a staged file into place fail."""
    real, seen = os.replace, []

    def replace(src, dst):
        seen.append(dst)
        if len(seen) == n:
            raise OSError(28, "No space left on device", str(dst))
        return real(src, dst)
    monkeypatch.setattr(atomic.os, "replace", replace)


# ------------------------------------------------------------ the two shapes

def test_write_all_puts_back_what_it_replaced_when_a_later_file_fails(tmp_path, monkeypatch):
    a, b, c = tmp_path / "a.yml", tmp_path / "b.yml", tmp_path / "new" / "c.yml"
    a.write_text("a\n"), b.write_text("b\n")
    _fail_the(3, monkeypatch)
    with pytest.raises(OSError):
        write_all({a: "A\n", b: "B\n", c: "C\n"})
    assert a.read_text() == "a\n" and b.read_text() == "b\n" and not c.exists()
    assert not [p for p in tmp_path.rglob("*") if p.name.endswith(atomic.STAGED)]


def test_write_all_writes_nothing_when_a_file_cannot_be_staged(tmp_path):
    a = tmp_path / "a.yml"
    a.write_text("a\n")
    blocked = tmp_path / "file-not-folder"
    blocked.write_text("x")
    with pytest.raises(OSError):
        write_all({a: "A\n", blocked / "b.yml": "B\n"})
    assert a.read_text() == "a\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.yml", "file-not-folder"]


def test_a_snapshot_puts_back_renamed_changed_added_and_removed_files(tmp_path):
    reg = tmp_path / "reqs"
    reg.mkdir()
    (reg / ".document.yml").write_text("prefix: REQ\n")
    (reg / "REQ-0001.yml").write_text("one\n")
    (reg / "REQ-0002.yml").write_text("two\n")
    cfg = tmp_path / "throughline.toml"
    cfg.write_text("x = 1\n")
    before = _tree(tmp_path)
    snapshot = Snapshot(files=[cfg], folders=[reg])
    (reg / ".document.yml").rename(reg / ".register.yml")
    (reg / "REQ-0001.yml").write_text("changed\n")
    (reg / "REQ-0002.yml").unlink()
    (reg / "REQ-0003.yml").write_text("added\n")
    cfg.write_text("x = 2\n")
    snapshot.restore()
    assert _tree(tmp_path) == before


# ----------------------------------------------------------------- migration

def _format_1(root: Path) -> Path:
    """A project as format 1 held it, whose last register holds a manifest under
    both names, so the step that renames them refuses there."""
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    for manifest in sorted(root.rglob(".register.yml")):
        manifest.rename(manifest.with_name(".document.yml"))
    cfg = root / "throughline.toml"
    cfg.write_text(re.sub(r"format_version = \d+", "format_version = 1",
                          cfg.read_text(encoding="utf-8")), encoding="utf-8")
    last = sorted(root.rglob(".document.yml"))[-1]
    last.with_name(".register.yml").write_bytes(last.read_bytes())
    return root


def test_a_migration_that_fails_part_way_leaves_the_project_as_it_was(tmp_path, capsys):
    """SR-0355: the manifests renamed before the refusal are put back."""
    root = _format_1(tmp_path / "p")
    before = _tree(root)
    assert _cli(["-C", root, "migrate"]) == 2
    got = capsys.readouterr()
    assert "already exists" in got.err and "nothing was migrated" in got.err
    assert got.err.count("\n") == 1 and "migrated project" not in got.out
    assert _tree(root) == before
    with pytest.raises(ProjectError):
        migrate_project(root)
    assert _tree(root) == before


def test_a_composed_migration_whose_sources_cannot_be_read_migrates_nothing(tmp_path, capsys):
    """SR-0355: the project's own pass and the union's are one migration."""
    root = tmp_path / "app"
    assert _cli(["-C", root, "init"]) == 0
    cfg = root / "throughline.toml"
    cfg.write_text(re.sub(r"format_version = \d+", "format_version = 3",
                          cfg.read_text(encoding="utf-8"))
                   + '\n[[sources]]\nnamespace = "lib"\npath = "../not-there"\n',
                   encoding="utf-8")
    before = _tree(root)
    capsys.readouterr()
    assert _cli(["-C", root, "migrate"]) != 0
    got = capsys.readouterr()
    assert "nothing was migrated" in got.err
    assert "migrated project from" not in got.out
    assert _tree(root) == before


# ------------------------------------------------------ a run over several items

def test_ratify_writes_no_item_when_one_cannot_be_written(tmp_path, monkeypatch, capsys):
    """SR-0356: the first item is not left signed beside an unsigned second."""
    root = _demo(tmp_path / "p")
    before = _tree(root)
    _fail_the(2, monkeypatch)
    assert _cli(["-C", root, "ratify", "REQ-0001", "REQ-0002", "REQ-0003",
                 "--by", "Ada"]) == 2
    got = capsys.readouterr()
    assert "nothing was written" in got.err and "ratified by" not in got.out
    assert _tree(root) == before


def test_ratify_writes_no_item_when_a_later_one_is_refused(tmp_path, monkeypatch, capsys):
    """SR-0356: a refusal reached only as a later item is signed leaves the
    earlier ones unsigned on disk."""
    import throughline.cli as climod
    root = _demo(tmp_path / "p")
    real, calls = climod.ratify, []

    def ratify(project, uid, *args, **kwargs):
        calls.append(uid)
        if len(calls) == 2:
            raise climod.GroundingError(f"{uid}: refused as it is signed")
        return real(project, uid, *args, **kwargs)
    monkeypatch.setattr(climod, "ratify", ratify)
    before = _tree(root)
    assert _cli(["-C", root, "ratify", "REQ-0001", "REQ-0002", "--by", "Ada"]) == 2
    got = capsys.readouterr()
    assert "nothing in this run was ratified" in got.err
    assert "ratified by" not in got.out
    assert _tree(root) == before


def test_a_ratified_deletion_and_its_dependents_are_written_together(tmp_path, monkeypatch):
    """SR-0356: the tombstone is not signed while a dependent stays unmarked."""
    root = _demo(tmp_path / "p")
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    assert _cli(["-C", root, "delete", "REQ-0001", "--reason", "gone"]) == 0
    before = _tree(root)
    _fail_the(2, monkeypatch)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada"]) == 2
    assert _tree(root) == before


def test_withdraw_and_invalidate_write_every_item_or_none(tmp_path, monkeypatch):
    root = _demo(tmp_path / "p")
    assert _cli(["-C", root, "ratify", "REQ-0001", "REQ-0002", "--by", "Ada"]) == 0
    before = _tree(root)
    _fail_the(2, monkeypatch)
    assert _cli(["-C", root, "withdraw", "REQ-0001", "REQ-0002", "--reason", "wrong",
                 "--by", "Ada"]) == 2
    assert _tree(root) == before
    monkeypatch.undo()
    _fail_the(2, monkeypatch)
    assert _cli(["-C", root, "invalidate", "REQ-0001"]) == 2    # marks TEST-0001
    assert _tree(root) == before


# ----------------------------------------------------------------- the schema

def test_an_attribute_and_its_values_are_withdrawn_together(tmp_path, monkeypatch, capsys):
    """SR-0356: the configuration is not left edited beside items that still
    carry the attribute it no longer declares."""
    root = _demo(tmp_path / "p")
    before = _tree(root)
    _fail_the(2, monkeypatch)
    assert _cli(["-C", root, "schema", "attr", "remove", "requirement", "priority",
                 "--unset", "--because", "not used"]) == 2
    assert "nothing was written" in capsys.readouterr().err
    assert _tree(root) == before
    monkeypatch.undo()
    assert _cli(["-C", root, "schema", "attr", "remove", "requirement", "priority",
                 "--unset", "--because", "not used"]) == 0
    assert "priority" not in (root / "requirements" / "REQ-0001.yml").read_text()
    assert _cli(["-C", root, "check"]) == 0


def test_a_schema_edit_that_would_not_read_back_is_not_written(tmp_path, monkeypatch, capsys):
    """SR-0356: the edited text is proved on the parse before it is written."""
    import throughline.schema_ops as ops
    root = _demo(tmp_path / "p")
    before = _tree(root)
    monkeypatch.setattr(ops.TomlDocument, "text", lambda self: "[status\nvalues = [")
    assert _cli(["-C", root, "schema", "status", "add", "parked",
                 "--because", "wanted"]) == 2
    assert "nothing was written" in capsys.readouterr().err
    assert _tree(root) == before


def test_a_schema_edit_the_editor_cannot_make_is_refused_in_one_line(tmp_path, capsys):
    """SR-0354: a type named with a quote stopped `tl schema type add` on an
    assertion (found on 6.0.0 by running every schema command over odd names)."""
    root = _demo(tmp_path / "p")
    before = _tree(root)
    capsys.readouterr()
    assert _cli(["-C", root, "schema", "type", "add", 'a"b', "--non-normative",
                 "--because", "why"]) == 2
    err = capsys.readouterr().err
    assert "cannot edit" in err and err.count("\n") == 1
    assert _tree(root) == before


# ------------------------------------------------------- the move to GQL filters

def test_the_move_to_gql_writes_the_configuration_and_documents_together(
        tmp_path, monkeypatch):
    root = tmp_path / "p"
    assert _cli(["-C", root, "init"]) == 0      # earlier language: see conftest
    before = _tree(root)
    _fail_the(2, monkeypatch)
    with pytest.raises(ProjectError, match="nothing was written"):
        from throughline.gql_migrate import apply_move, plan_move
        apply_move(plan_move(root))
    assert _tree(root) == before
