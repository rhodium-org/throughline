# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A repair `tl migrate` would apply is reached from `tl check` and from
`tl migrate --beneath`, not only by a hand-run `tl migrate` (SR-0301, SR-0304,
SR-0305).

On 2 November 2026 throughline 4.7.0 reported 4,470 unsigned tombstones across 41
graphs at format 4. SR-0273's backfill signed all but six, but nothing pointed at
it: `tl check` never named `tl migrate`, and `--beneath` listed no graph because
every one was already at the current format.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from throughline.cli import main as cli_main
from throughline.migrate_beneath import survey
from throughline.storage import load_project, migrate_project


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, check=True).stdout


def _commit(root: Path, message: str) -> None:
    _git(root, "add", ".")
    _git(root, "commit", "-qm", message)


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts}


@pytest.fixture
def graph(tmp_path) -> Path:
    """A committed graph whose only defect is a tombstone written before a deletion
    had to be signed: committed unsigned, so history can attribute it."""
    root = tmp_path / "proj"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "Why",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "Base", "--text", "Base.",
                 "--ground", "INT-0001", "--origin", "ai", "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "Gone", "--text", "Gone.",
                 "--ground", "INT-0001", "--origin", "ai", "--no-interactive"]) == 0
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    assert _cli(["-C", root, "ratify", "REQ-0002", "--by", "Ada"]) == 0
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "grace@example.com")
    _git(root, "config", "user.name", "Grace")
    _commit(root, "baseline")
    assert _cli(["-C", root, "delete", "REQ-0002", "--reason", "superseded"]) == 0
    _commit(root, "delete REQ-0002")
    return root


def _strip_revisions(root: Path) -> None:
    """Remove every cached revision (SR-0166), as a signature tl-ratify gives
    leaves it, so the cache is the only thing tl migrate would write."""
    for path in root.rglob("*.yml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        for entry in data.get("ratifications") or []:
            entry.pop("revision", None)
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                        encoding="utf-8")


# ----------------------------------------------------------------- SR-0301

def test_beneath_lists_a_current_graph_a_repair_would_change_and_writes_nothing(
        graph, capsys):
    before = _tree(graph)
    [found] = survey([graph])
    assert found.behind
    assert found.repairs == ("tombstones signed from history",)

    assert _cli(["migrate", "--beneath", graph]) == 0
    out = capsys.readouterr().out
    assert "1 graph(s) behind" in out
    assert ".  format 4  repairs: tombstones signed from history" in out
    assert "0 graph(s) at format version 4 with nothing to repair" in out
    assert _tree(graph) == before
    assert _git(graph, "status", "--porcelain") == ""


def test_beneath_does_not_list_a_graph_that_would_gain_only_a_cached_revision(graph):
    migrate_project(graph)
    _strip_revisions(graph)
    _commit(graph, "sign the tombstone; drop the caches")
    [found] = survey([graph])
    assert found.repairs == ()
    assert not found.behind


# ----------------------------------------------------------------- SR-0304

def test_apply_migrates_a_current_graph_a_repair_would_change(graph, capsys):
    assert _cli(["migrate", "--beneath", graph, "--apply"]) == 0
    assert f"migrated 1 graph(s) in {graph.resolve()}" in capsys.readouterr().out
    _cli(["-C", graph, "check", "--strict"])
    assert "deletion awaiting" not in capsys.readouterr().out
    assert _git(graph, "log", "--format=%s", "-1").strip() == "delete REQ-0002"


# ----------------------------------------------------------------- SR-0305

def test_check_names_migrate_beside_the_findings_it_resolves(graph, capsys):
    before = _tree(graph)
    assert _cli(["-C", graph, "check", "--strict"]) == 1
    captured = capsys.readouterr()
    assert "REQ-0002" in captured.out and "deletion awaiting" in captured.out
    assert "1 of these finding(s) `tl migrate` resolves" in captured.err
    assert f"tl -C {graph} migrate" in captured.err
    assert _tree(graph) == before


def test_check_says_nothing_of_migrate_when_it_resolves_none(graph, capsys):
    # The starter schema's coverage rule still fails REQ-0001, which no repair clears.
    migrate_project(graph)
    assert _cli(["-C", graph, "check", "--strict"]) == 1
    captured = capsys.readouterr()
    assert "coverage" in captured.out and "deletion awaiting" not in captured.out
    assert "tl migrate" not in captured.err


def test_check_says_nothing_of_migrate_for_a_finding_no_repair_clears(graph, capsys):
    migrate_project(graph)
    assert _cli(["-C", graph, "new", "REQ", "--title", "New", "--text", "New.",
                 "--ground", "INT-0001", "--origin", "ai", "--no-interactive"]) == 0
    assert _cli(["-C", graph, "check", "--strict"]) == 1
    assert "tl migrate" not in capsys.readouterr().err
