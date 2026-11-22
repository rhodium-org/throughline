# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A project whose filters are GQL declares format version 5 (SR-0346).

A tl older than GQL filters reads each one as a filter of the earlier language
that fails to parse and selects nothing: a wrong answer and no refusal. The
format version is what an older tl already refuses on, so a project in GQL says
5 and one in the earlier language may stay at 4, read as it always was.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.storage import (EARLIER_FORMAT_VERSION, FORMAT_VERSION,
                                 GQL_FORMAT_VERSION, FormatError, load_project,
                                 migrate_project, read_project)


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _declared(root: Path) -> int:
    m = re.search(r"^format_version = (\d+)",
                  (root / "throughline.toml").read_text(encoding="utf-8"), re.M)
    return int(m.group(1))


def _declare(root: Path, version: int) -> None:
    cfg = root / "throughline.toml"
    cfg.write_text(re.sub(r"format_version = \d+", f"format_version = {version}",
                          cfg.read_text(encoding="utf-8")), encoding="utf-8")


def _unbuilt(root: Path) -> None:
    """Leave out the built role `tl init` binds, so a project's format is decided
    by its filters alone (SR-0385 decides it where the role is named)."""
    cfg = root / "throughline.toml"
    cfg.write_text(re.sub(r'(?m)^built = \[.*\]\n', "", cfg.read_text(encoding="utf-8")),
                   encoding="utf-8")


def _gql(tmp_path: Path, version: int = GQL_FORMAT_VERSION) -> Path:
    root = tmp_path / "gql"
    assert _cli(["-C", root, "init", "--name", "t"]) == 0
    _unbuilt(root)
    _declare(root, version)
    return root


def _earlier(tmp_path: Path, version: int = EARLIER_FORMAT_VERSION) -> Path:
    """A project as every one was before GQL: no key, filters in the earlier
    language, format version 4."""
    root = tmp_path / "earlier"
    assert _cli(["-C", root, "init", "--name", "t"]) == 0
    cfg = root / "throughline.toml"
    text = cfg.read_text(encoding="utf-8")
    start = text.index("# The language this project's filters are in")
    end = text.index('filter_language = "gql"\n') + len('filter_language = "gql"\n')
    cfg.write_text((text[:start] + text[end:]).replace(
        "item.type = 'requirement' AND item.status <> 'deleted'",
        "type == 'requirement' and status != 'deleted'"), encoding="utf-8")
    doc = root / "docs" / "overview.md"
    doc.write_text(doc.read_text(encoding="utf-8").replace("item.type = '", "type == '"),
                   encoding="utf-8")
    _unbuilt(root)
    _declare(root, version)
    assert _cli(["-C", root, "docs"]) == 0
    return root


def _files(root: Path) -> dict:
    return {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_the_versions_are_four_five_and_six():
    assert (EARLIER_FORMAT_VERSION, GQL_FORMAT_VERSION, FORMAT_VERSION) == (4, 5, 6)


def test_init_writes_the_version_its_configuration_needs(tmp_path):
    # GQL filters and a built role: version 6 (SR-0346, SR-0385).
    root = tmp_path / "gql"
    assert _cli(["-C", root, "init", "--name", "t"]) == 0
    assert _declared(root) == 6
    project = load_project(root)
    assert project.filter_language == "gql" and project.schema.built_order
    assert _cli(["-C", root, "check"]) == 0


def test_init_from_a_schema_writes_version_five(tmp_path):
    standard = _gql(tmp_path)
    root = tmp_path / "follower"
    assert _cli(["-C", root, "init", "--from-schema", standard,
                 "--namespace", "std"]) == 0
    assert _declared(root) == 5


def test_a_project_at_four_in_the_earlier_language_is_read_as_it_is(tmp_path, capsys):
    root = _earlier(tmp_path)
    before = _files(root)
    assert load_project(root).filter_language == "python"
    assert _cli(["-C", root, "check"]) == 0
    assert _cli(["-C", root, "docs", "--check"]) == 0
    # Not asked to migrate (SR-0346); the built axis is offered (SR-0386).
    err = capsys.readouterr().err
    assert "run `tl migrate`" not in err and "--to-gql" not in err
    assert _files(root) == before


def test_tl_migrate_takes_a_project_at_four_to_what_it_then_needs(tmp_path):
    """Reading never moves a project (SR-0346); migrating binds the built role
    where implemented and verified are declared (SR-0377), and the version says
    so (SR-0385). Run again, it moves nothing."""
    root = _earlier(tmp_path)
    assert load_project(root) is not None and _declared(root) == 4
    assert _cli(["-C", root, "migrate"]) == 0
    assert _declared(root) == 6
    assert migrate_project(root)[:2] == (6, 6)


def test_a_project_in_the_earlier_language_is_read_at_five(tmp_path):
    root = _earlier(tmp_path, version=5)
    assert load_project(root).filter_language == "python"
    assert _cli(["-C", root, "check"]) == 0


def test_gql_at_four_is_refused_saying_so(tmp_path, capsys):
    root = _gql(tmp_path, version=4)
    with pytest.raises(FormatError) as caught:
        load_project(root)
    said = str(caught.value)
    assert "gql" in said and "format version 4" in said
    assert "format version 5" in said and "tl migrate" in said
    assert (caught.value.declared, caught.value.implemented) == (4, FORMAT_VERSION)
    capsys.readouterr()
    assert _cli(["-C", root, "check"]) != 0
    assert "format version 5" in capsys.readouterr().err


def test_gql_at_four_is_refused_as_a_source_too(tmp_path):
    root = _gql(tmp_path, version=4)
    with pytest.raises(FormatError):
        read_project(root)


def test_tl_migrate_raises_a_gql_project_at_four(tmp_path):
    """What tl 5.9 wrote: the language without the version. At least 5 for the
    language (SR-0346); 6 once the built role is bound (SR-0385)."""
    root = _gql(tmp_path, version=4)
    assert _cli(["-C", root, "migrate"]) == 0
    assert _declared(root) == 6
    assert _cli(["-C", root, "check"]) == 0


def test_the_move_to_gql_sets_the_version_with_the_language(tmp_path):
    root = _earlier(tmp_path)
    assert _cli(["-C", root, "migrate", "--to-gql"]) == 0
    assert _declared(root) == 5
    assert load_project(root).filter_language == "gql"
    assert _cli(["-C", root, "check"]) == 0
    assert _cli(["-C", root, "docs", "--check"]) == 0


def test_a_dry_run_of_the_move_leaves_the_version(tmp_path):
    root = _earlier(tmp_path)
    before = _files(root)
    assert _cli(["-C", root, "migrate", "--to-gql", "--dry-run"]) == 0
    assert _files(root) == before


def test_a_version_above_six_is_refused_naming_the_upgrade(tmp_path):
    root = _gql(tmp_path, version=7)
    with pytest.raises(FormatError) as caught:
        load_project(root)
    assert "upgrade" in str(caught.value)
    assert (caught.value.declared, caught.value.implemented) == (7, 6)


def test_a_version_below_four_is_still_sent_to_migrate(tmp_path):
    root = _earlier(tmp_path)
    _declare(root, 3)
    with pytest.raises(FormatError) as caught:
        load_project(root)
    assert "tl migrate" in str(caught.value)
    assert migrate_project(root)[:2] == (3, 6)
    assert _declared(root) == 6


def test_dump_says_the_version_the_project_declares(tmp_path, capsys):
    for root, version in ((_earlier(tmp_path), 4), (_gql(tmp_path), 5)):
        capsys.readouterr()
        assert _cli(["-C", root, "dump"]) == 0
        dumped = json.loads(capsys.readouterr().out)
        assert dumped["throughline_dump"]["format_version"] == version


def test_beneath_counts_both_versions_as_current(tmp_path, capsys):
    _earlier(tmp_path)
    _gql(tmp_path)
    _gql(tmp_path / "held", version=4)
    capsys.readouterr()
    assert _cli(["migrate", "--beneath", tmp_path]) == 0
    said = capsys.readouterr().out
    assert "newer than this tl" not in said
    assert "1 graph(s) already at format version 4" in said
    assert "1 graph(s) already at format version 5" in said
    assert "1 graph(s) below the format version their filters or their built " \
        "role need" in said
