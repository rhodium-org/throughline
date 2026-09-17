# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""The baseline check measures the working tree against (SR-0083, SR-0093): read
whether or not transitions are declared, from git or from a directory a host
supplies (SR-0210), and never silently absent (SR-0209).

Where git was missing, `tl check` printed an unqualified pass over two rules it
had not run (issue #5), and a project declaring no transitions skipped tombstone
permanence even inside git.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.storage import load_project, read_baseline


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, check=True).stdout


def _commit(root: Path) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "baseline")


def _repo(root: Path, *, transitions: bool = True) -> Path:
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    if not transitions:
        cfg = root / "throughline.toml"
        text = re.sub(r"\n\[transitions\]\n(?:(?!\[).*\n)*", "\n",
                      cfg.read_text(encoding="utf-8"))
        cfg.write_text(text, encoding="utf-8")
        assert load_project(root).schema.transitions is None
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "Why",
                 "--no-interactive"]) == 0
    for title in ("Moved", "Retired"):
        assert _cli(["-C", root, "new", "REQ", "--title", title, "--text", "t.",
                     "--ground", "INT-0001", "--no-interactive"]) == 0
    if transitions:
        assert _cli(["-C", root, "status", "REQ-0001", "approved"]) == 0
        assert _cli(["-C", root, "status", "REQ-0001", "implemented"]) == 0
    assert _cli(["-C", root, "delete", "REQ-0002", "--reason", "gone"]) == 0
    _commit(root)
    return root


def _drift(root: Path) -> None:
    """An illegal status move and an erased tombstone, not yet committed."""
    moved = root / "requirements" / "REQ-0001.yml"
    moved.write_text(moved.read_text(encoding="utf-8").replace(
        "status: implemented", "status: draft"), encoding="utf-8")
    (root / "requirements" / "REQ-0002.yml").unlink()


def _copy_without_git(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git"))
    return dst


@pytest.fixture
def repo(tmp_path) -> Path:
    return _repo(tmp_path / "repo")


def test_git_baseline_runs_both_rules_and_says_nothing_more(repo, capsys):
    _drift(repo)
    assert _cli(["-C", repo, "check"]) == 1
    out = capsys.readouterr()
    assert "bad-transition" in out.out and "tombstone-deleted" in out.out
    assert "not checked" not in out.err


def test_outside_git_the_result_names_the_rules_that_did_not_run(repo, tmp_path, capsys):
    work = _copy_without_git(repo, tmp_path / "work")
    _drift(work)
    rc = _cli(["-C", work, "check"])
    out = capsys.readouterr()
    assert "bad-transition" not in out.out and "tombstone-deleted" not in out.out
    assert ("not checked: transition legality and tombstone permanence — "
            "the project is not in a git work tree") in out.err
    # Not a finding: the exit status is what the findings alone make it.
    assert rc == _cli(["-C", work, "check", "--base", ""])


def test_json_stays_a_list_of_findings_and_the_note_goes_to_stderr(repo, tmp_path, capsys):
    work = _copy_without_git(repo, tmp_path / "work")
    capsys.readouterr()
    _cli(["-C", work, "check", "--format", "json"])
    out = capsys.readouterr()
    assert isinstance(json.loads(out.out), list)
    assert "not checked:" in out.err


def test_a_disabled_or_unresolvable_baseline_is_named(repo, capsys):
    _cli(["-C", repo, "check", "--base", ""])
    assert "the baseline was disabled" in capsys.readouterr().err
    _cli(["-C", repo, "check", "--base", "no-such-ref"])
    assert "the revision 'no-such-ref' cannot be resolved" in capsys.readouterr().err


def test_a_repository_with_no_commits_is_a_baseline_where_everything_is_new(tmp_path, capsys):
    root = tmp_path / "fresh"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    _git(root, "init", "-q")
    assert read_baseline(load_project(root)).statuses == {}
    capsys.readouterr()
    _cli(["-C", root, "check"])
    assert "not checked" not in capsys.readouterr().err


def test_a_baseline_directory_runs_both_rules_outside_git(repo, tmp_path, capsys):
    at_base = _copy_without_git(repo, tmp_path / "at-base")
    work = _copy_without_git(repo, tmp_path / "work")
    _drift(work)
    assert _cli(["-C", work, "check", "--base-dir", at_base]) == 1
    out = capsys.readouterr()
    assert "bad-transition" in out.out and "tombstone-deleted" in out.out
    assert "not checked" not in out.err


def test_a_baseline_directory_that_does_not_exist_fails_the_command(repo, tmp_path, capsys):
    assert _cli(["-C", repo, "check", "--base-dir", tmp_path / "missing"]) == 2
    assert "does not exist" in capsys.readouterr().err


def test_git_and_a_directory_read_the_same_baseline(repo, tmp_path):
    """One reading of a baseline, whichever source supplies the files."""
    at_base = _copy_without_git(repo, tmp_path / "at-base")
    _drift(repo)
    project = load_project(repo)
    from_git = read_baseline(project, ref="HEAD")
    from_dir = read_baseline(project, base_dir=at_base)
    assert from_git.statuses == from_dir.statuses
    assert from_git.statuses == {"INT-0001": "draft", "REQ-0001": "implemented",
                                 "REQ-0002": "deleted"}


def test_tombstone_permanence_runs_without_a_transitions_table(tmp_path, capsys):
    """SR-0093 sets no condition on transitions; the gate used to."""
    root = _repo(tmp_path / "repo", transitions=False)
    (root / "requirements" / "REQ-0002.yml").unlink()
    assert _cli(["-C", root, "check"]) == 1
    assert "tombstone-deleted" in capsys.readouterr().out
    work = _copy_without_git(root, tmp_path / "work")
    _cli(["-C", work, "check"])
    assert ("not checked: tombstone permanence — the project is not in a git "
            "work tree") in capsys.readouterr().err
