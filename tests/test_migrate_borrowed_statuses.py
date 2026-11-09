# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""SR-0319 — `tl migrate` moves the items of a graph that borrows its statuses
from a schema source out of the statuses SR-0313 retires.

A conformance example that takes its schema from the standard beside it (`path =
"../.."`, `schema = true`) declares no lifecycle of its own, so SR-0313 left it to
its source and SR-0316 read nothing to retire from its configuration: once the
source was migrated, every item still in draft failed `tl check` with bad-status.
Found migrating rhodium-org/standards-iddn-uk on 9 November 2026.
"""
from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

from test_retire_unsigned_agreement import _item, _old_graph
from throughline.cli import main as cli_main
from throughline.migrate_beneath import survey
from throughline.storage import load_project, migrate_project
from throughline.validate import validate

_CHILD = """[project]
name = "example"
format_version = 4

[[sources]]
namespace = "std"
path = "../.."
schema = true
"""


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _family(tmp_path: Path) -> tuple[Path, Path]:
    """A pre-5.0 graph and an example two levels beneath it that borrows its
    schema, with items in draft and approved."""
    parent = _old_graph(tmp_path)
    child = parent / "examples" / "one"
    (child / "requirements").mkdir(parents=True)
    (child / "requirements" / ".register.yml").write_text(
        "prefix: EX\ndigits: 4\n", encoding="utf-8")
    (child / "throughline.toml").write_text(_CHILD, encoding="utf-8")
    grounded = "links:\n- target: std:INT-0001\n  type: derives_from\n"
    _item(child, "FR-0001", "draft", grounded)
    _item(child, "FR-0002", "approved", grounded)
    _item(child, "FR-0003", "implemented", grounded)
    # Items written by _item land in a register by prefix; rename them into EX.
    for uid in ("FR-0001", "FR-0002", "FR-0003"):
        path = child / "requirements" / f"{uid}.yml"
        ex = uid.replace("FR", "EX")
        path.with_name(f"{ex}.yml").write_text(
            path.read_text(encoding="utf-8").replace(uid, ex), encoding="utf-8")
        path.unlink()
    return parent, child


def _statuses(root: Path) -> dict[str, str]:
    return {i.uid: i.status for i in load_project(root).items()
            if ":" not in i.uid}


def _bad_status(root: Path) -> list[str]:
    return [f.uid for f in validate(load_project(root)) if f.rule == "bad-status"]


def _in_repo(root: Path) -> Path:
    for args in (["init", "-q"], ["config", "user.email", "grace@example.com"],
                 ["config", "user.name", "Grace"], ["add", "."],
                 ["commit", "-qm", "baseline"]):
        subprocess.run(["git", "-C", str(root), *args], check=True,
                       capture_output=True)
    return root


def test_the_child_is_moved_when_its_source_is_migrated_first(tmp_path):
    """The order `--beneath` takes: the source's lifecycle has already lost the
    statuses, so the child's items hold ones the schema no longer declares."""
    parent, child = _family(tmp_path)
    migrate_project(parent)
    assert set(_bad_status(child)) == {"EX-0001", "EX-0002"}
    config = (child / "throughline.toml").read_text(encoding="utf-8")
    result = migrate_project(child)
    assert set(result.retired_statuses) == {"draft", "approved"}
    assert result.moved == {"EX-0001": "draft -> proposed",
                            "EX-0002": "approved -> proposed"}
    assert _statuses(child) == {"EX-0001": "proposed", "EX-0002": "proposed",
                                "EX-0003": "implemented"}
    assert not _bad_status(child)
    # The lifecycle is the source's: the child's configuration is not written.
    assert (child / "throughline.toml").read_text(encoding="utf-8") == config


def test_the_child_is_moved_before_its_source_is_migrated(tmp_path):
    """By SR-0313's rule, read from the schema the child borrows."""
    parent, child = _family(tmp_path)
    result = migrate_project(child)
    assert set(result.retired_statuses) == {"draft", "approved"}
    assert _statuses(child)["EX-0001"] == "proposed"
    migrate_project(parent)
    assert not _bad_status(child)


def test_the_repair_is_idempotent(tmp_path, capsys):
    parent, child = _family(tmp_path)
    migrate_project(parent)
    assert _cli(["-C", child, "migrate"]) == 0
    out = capsys.readouterr().out
    assert "retired draft, approved" in out and "EX-0001: draft -> proposed" in out
    assert _cli(["-C", child, "migrate"]) == 0
    assert "nothing to migrate" in capsys.readouterr().out


def test_beneath_lists_and_applies_the_child(tmp_path, capsys):
    parent, child = _family(tmp_path)
    _in_repo(parent)
    found = {f.root: f for f in survey([parent])}
    assert found[child].retires == ("draft", "approved") and found[child].due

    assert _cli(["migrate", "--beneath", parent]) == 0
    out = capsys.readouterr().out
    assert "2 graph(s) at format version 4 that still hold" in out
    assert f"{child}  draft, approved" in out

    assert _cli(["migrate", "--beneath", parent, "--apply"]) == 0
    assert "migrated 2 graph(s)" in capsys.readouterr().out
    assert not _bad_status(child) and not _bad_status(parent)
    assert not any(f.due for f in survey([parent]))
    values = tomllib.loads((parent / "throughline.toml").read_text(encoding="utf-8"))
    assert not {"approved", "draft"} & set(values["status"]["values"])
