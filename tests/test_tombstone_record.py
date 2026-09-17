# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A tombstone records the day its UID was retired, why, and the fingerprint of the
content it last held (SR-0012), and is never rewritten (SR-0093).

Before 3.6.2 `tl delete` recorded only a reason, although SR-0012 and doc 06 both
say a tombstone keeps its deletion date and last content hash. Tombstones written
then are permanent too, so nothing here may rewrite them.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from throughline.cli import main as cli_main
from throughline.fingerprint import fingerprint
from throughline.storage import load_project, migrate_project
from throughline.validate import validate


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


@pytest.fixture
def graph(tmp_path) -> Path:
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "Why",
                 "--no-interactive"]) == 0
    for title in ("Kept", "Retired"):
        assert _cli(["-C", root, "new", "REQ", "--title", title, "--text", f"{title}.",
                     "--ground", "INT-0001", "--no-interactive"]) == 0
    return root


def _file(root: Path, uid: str) -> Path:
    return next(root.rglob(f"{uid}.yml"))


def test_delete_records_the_day_the_reason_and_the_last_fingerprint(graph):
    project = load_project(graph)
    want = fingerprint(project.get("REQ-0002"), project.schema)
    assert _cli(["-C", graph, "delete", "REQ-0002", "--reason", "superseded"]) == 0
    record = load_project(graph).get("REQ-0002").deleted
    assert record["reason"] == "superseded"
    assert record["fingerprint"] == want
    # A string in the file, not a YAML date, so it survives every reader unchanged.
    raw = yaml.safe_load(_file(graph, "REQ-0002").read_text(encoding="utf-8"))
    assert isinstance(raw["deleted"]["date"], str)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", record["date"])
    today = datetime.now(timezone.utc).date()
    assert date.fromisoformat(record["date"]) in {today - timedelta(days=1), today}


def test_deleting_a_tombstone_again_changes_nothing(graph, capsys):
    assert _cli(["-C", graph, "delete", "REQ-0002", "--reason", "superseded"]) == 0
    before = _file(graph, "REQ-0002").read_bytes()
    capsys.readouterr()
    assert _cli(["-C", graph, "delete", "REQ-0002", "--reason", "a second opinion"]) == 0
    assert "already deleted" in capsys.readouterr().out
    assert _file(graph, "REQ-0002").read_bytes() == before


def test_a_tombstone_from_an_earlier_release_is_left_as_it_is(graph):
    """Backward compatibility: a reason-only tombstone raises nothing and is never
    rewritten, by check or by migrate."""
    assert _cli(["-C", graph, "delete", "REQ-0002", "--reason", "dropped"]) == 0
    path = _file(graph, "REQ-0002")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["deleted"] = {"reason": "dropped"}          # the shape 3.6.1 wrote
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    old = path.read_bytes()
    findings = {(f.rule, f.uid) for f in validate(load_project(graph))}
    assert not {f for f in findings if f[1] == "REQ-0002"}
    migrate_project(graph)
    assert path.read_bytes() == old
