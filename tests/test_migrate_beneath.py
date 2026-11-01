# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""`tl migrate --beneath` finds every graph beneath some paths, lists the ones
behind the current format, and with --apply migrates those safe to rewrite
(UR-0045, SR-0300..SR-0304).

Asked on 1 November 2026, when tl-ratify found 268 of 430 graphs unreadable
because they were still at format 3, many of them in other sessions' worktrees.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.discovery import graphs_beneath
from throughline.storage import FORMAT_VERSION


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, check=True).stdout


def _repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "grace@example.com")
    _git(root, "config", "user.name", "Grace")
    return root


def _commit(root: Path) -> None:
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "baseline")


def _graph(root: Path, version: int = 3) -> Path:
    """A graph declaring ``version`` — made at the current format and relabelled,
    which is all the 3-to-4 step needs to find in front of it."""
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    cfg = root / "throughline.toml"
    cfg.write_text(re.sub(r"format_version = \d+", f"format_version = {version}",
                          cfg.read_text(encoding="utf-8")), encoding="utf-8")
    return root


def _declared(root: Path) -> int:
    m = re.search(r"format_version = (\d+)",
                  (root / "throughline.toml").read_text(encoding="utf-8"))
    return int(m.group(1))


# ----------------------------------------------------------------- SR-0300

def test_discovery_finds_nested_graphs_and_skips_what_no_one_owns(tmp_path):
    repo = _repo(tmp_path / "repo")
    found = [_graph(repo / "idd"), _graph(repo / "packages" / "a" / "idd")]
    _graph(repo / ".hidden" / "idd")
    _graph(repo / "venv" / "idd")
    (repo / "venv" / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    _graph(repo / "build" / "idd")
    (repo / ".gitignore").write_text("build/\n", encoding="utf-8")
    os.symlink(repo / "idd", repo / "linked")
    _graph(repo / "a" / "b" / "c" / "d" / "e" / "too-deep")

    assert graphs_beneath(repo) == found


def test_discovery_leaves_out_a_graph_an_enclosing_graph_borrows(tmp_path):
    outer = _graph(tmp_path / "outer")
    # Made apart and moved in, because init refuses to nest one graph in another.
    shutil.move(_graph(tmp_path / "made-apart"), outer / "baseline")
    cfg = outer / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8")
                   + '\n[[sources]]\nnamespace = "baseline"\npath = "baseline"\n',
                   encoding="utf-8")
    assert graphs_beneath(tmp_path) == [outer]


def test_discovery_lists_a_graph_two_paths_reach_once(tmp_path):
    g = _graph(tmp_path / "x" / "idd")
    assert graphs_beneath(tmp_path, tmp_path / "x") == [g]


# ----------------------------------------------------------------- SR-0301

def test_without_apply_it_lists_by_repository_and_writes_nothing(tmp_path, capsys):
    one = _repo(tmp_path / "one")
    _graph(one / "idd")
    _graph(one / "docs" / "idd")
    two = _repo(tmp_path / "two")
    _graph(two / "idd", version=FORMAT_VERSION)
    _commit(one)
    before = (one / "idd" / "throughline.toml").read_text(encoding="utf-8")

    assert _cli(["migrate", "--beneath", tmp_path]) == 0
    out = capsys.readouterr().out
    assert "2 graph(s) below format version" in out
    assert f"  {one.resolve()}\n    idd  format 3\n    docs/idd  format 3\n" in out
    assert f"1 graph(s) already at format version {FORMAT_VERSION}" in out
    assert "nothing was written" in out
    assert (one / "idd" / "throughline.toml").read_text(encoding="utf-8") == before
    assert _git(one, "status", "--porcelain") == ""


def test_without_apply_it_names_graphs_it_cannot_read_or_that_are_newer(tmp_path,
                                                                       capsys):
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "throughline.toml").write_text("[project\n",
                                                           encoding="utf-8")
    _graph(tmp_path / "future", version=FORMAT_VERSION + 1)
    assert _cli(["migrate", "--beneath", tmp_path]) == 0
    out = capsys.readouterr().out
    assert "1 graph(s) whose configuration cannot be read" in out
    assert str((tmp_path / "broken").resolve()) in out
    assert "newer than this tl" in out
    assert f"format {FORMAT_VERSION + 1}" in out


def test_apply_without_beneath_is_a_usage_error(tmp_path, capsys):
    _graph(tmp_path)
    assert _cli(["-C", tmp_path, "migrate", "--apply"]) == 2
    assert "--beneath" in capsys.readouterr().err
    assert _declared(tmp_path) == 3


# ----------------------------------------------------------------- SR-0302

@pytest.mark.parametrize("change", ["tracked", "untracked"])
def test_apply_skips_a_graph_with_uncommitted_changes(tmp_path, capsys, change):
    repo = _repo(tmp_path / "repo")
    g = _graph(repo / "idd")
    _commit(repo)
    if change == "tracked":
        (g / "throughline.toml").write_text(
            (g / "throughline.toml").read_text(encoding="utf-8") + "# edit\n",
            encoding="utf-8")
    else:
        (g / "notes.txt").write_text("wip\n", encoding="utf-8")

    assert _cli(["migrate", "--beneath", repo, "--apply"]) == 0
    out = capsys.readouterr().out
    assert f"{g.resolve()} — has uncommitted changes" in out
    assert "migrated nothing" in out
    assert _declared(g) == 3


def test_apply_skips_a_graph_outside_any_repository(tmp_path, capsys):
    g = _graph(tmp_path / "loose")
    assert _cli(["migrate", "--beneath", tmp_path, "--apply"]) == 0
    assert f"{g.resolve()} — not in a git repository" in capsys.readouterr().out
    assert _declared(g) == 3


# ----------------------------------------------------------------- SR-0303

def test_apply_skips_a_graph_in_a_linked_worktree(tmp_path, capsys):
    repo = _repo(tmp_path / "repo")
    _graph(repo / "idd")
    _commit(repo)
    linked = tmp_path / "repo-worktrees" / "feature"
    _git(repo, "worktree", "add", "-q", "-b", "feature", str(linked))

    assert _cli(["migrate", "--beneath", tmp_path / "repo-worktrees", "--apply"]) == 0
    out = capsys.readouterr().out
    assert f"{(linked / 'idd').resolve()} — in a linked git worktree" in out
    assert _declared(linked / "idd") == 3


# ----------------------------------------------------------------- SR-0304

def test_apply_migrates_the_rest_commits_nothing_and_carries_on(tmp_path, capsys):
    repo = _repo(tmp_path / "repo")
    a = _graph(repo / "a")
    b = _graph(repo / "b")
    bad = _graph(repo / "c", version=0)   # no migration path from format 0
    _commit(repo)
    head = _git(repo, "rev-parse", "HEAD")

    assert _cli(["migrate", "--beneath", repo, "--apply"]) == 1
    out = capsys.readouterr().out
    assert _declared(a) == _declared(b) == FORMAT_VERSION
    assert f"{bad.resolve()} — no migration path from format version 0" in out
    assert (f"migrated 2 graph(s) in {repo.resolve()} to format version "
            f"{FORMAT_VERSION}") in out
    assert _git(repo, "rev-parse", "HEAD") == head
    assert _git(repo, "status", "--porcelain") != ""


def test_apply_reports_one_line_per_repository(tmp_path, capsys):
    for name in ("one", "two"):
        repo = _repo(tmp_path / name)
        _graph(repo / "idd")
        _commit(repo)
    assert _cli(["migrate", "--beneath", tmp_path, "--apply"]) == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines()
             if ln.startswith("migrated 1 graph(s) in ")]
    assert len(lines) == 2
