# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""`tl resolve` (SR-0248) and the file-name rule it rests on (SR-0249).

Every scenario is a real git merge that stops on a real add/add conflict: two
branches allocate the same UID from the same starting point, exactly as `tl new`
does when it numbers from whatever is on the branch it runs on.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.collision import CollisionError, rewrite
from throughline.storage import load_project
from throughline.validate import validate


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, check=check)


def _new(root: Path, title: str, *, ground: str = "INT-0001") -> None:
    assert _cli(["-C", root, "new", "REQ", "--title", title, "--text", "t.",
                 "--ground", ground, "--no-interactive"]) == 0


def _commit(root: Path, msg: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", msg)


def _item(root: Path, uid: str) -> str:
    return (root / "requirements" / f"{uid}.yml").read_text(encoding="utf-8")


@pytest.fixture
def repo(tmp_path) -> Path:
    """main holds INT-0001 and REQ-0001; `feat` branches off it."""
    root = tmp_path / "repo"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "Why",
                 "--no-interactive"]) == 0
    _new(root, "Base")
    _commit(root, "base")
    _git(root, "branch", "feat")
    return root


def _collide(root: Path) -> None:
    """Both branches allocate REQ-0002; feat also links REQ-0003 to its own."""
    _git(root, "checkout", "-q", "feat")
    _new(root, "Feat item")
    _new(root, "Feat dependent", ground="REQ-0002")
    _commit(root, "feat: add REQ-0002 and REQ-0003")
    _git(root, "checkout", "-q", "main")
    _new(root, "Main item")
    _commit(root, "main: add REQ-0002")


def _merge(root: Path, branch: str) -> None:
    done = _git(root, "merge", branch, check=False)
    assert done.returncode != 0 and "CONFLICT (add/add)" in done.stdout


def test_merging_trunk_into_a_branch_moves_the_branch_item(repo, capsys):
    _collide(repo)
    _git(repo, "checkout", "-q", "feat")
    _merge(repo, "main")

    assert _cli(["-C", repo, "resolve"]) == 0
    out = capsys.readouterr().out
    assert "kept   REQ-0002  (theirs, already on main)  Main item" in out
    assert "move REQ-0002 -> REQ-0004  (ours)  Feat item" in out

    assert "title: Main item" in _item(repo, "REQ-0002")
    moved = _item(repo, "REQ-0004")
    assert moved.startswith("uid: REQ-0004\n") and "title: Feat item" in moved
    # feat's dependent meant feat's item, so it follows the move
    assert "target: REQ-0004" in _item(repo, "REQ-0003")
    assert _git(repo, "ls-files", "-u").stdout == ""          # nothing left unmerged
    assert "run `tl check --base MERGE_HEAD`" in out
    assert _cli(["-C", repo, "check", "--base", "MERGE_HEAD"]) == 0
    _git(repo, "commit", "-qm", "merge main")
    assert _cli(["-C", repo, "check"]) == 0


def test_merging_a_branch_into_trunk_keeps_ours(repo, capsys):
    _collide(repo)
    _merge(repo, "feat")                                     # on main

    assert _cli(["-C", repo, "resolve"]) == 0
    out = capsys.readouterr().out
    assert "kept   REQ-0002  (ours, already on main)  Main item" in out
    assert "title: Main item" in _item(repo, "REQ-0002")
    assert "title: Feat item" in _item(repo, "REQ-0004")
    assert "target: REQ-0004" in _item(repo, "REQ-0003")
    assert "run `tl check`, then" in out
    assert _cli(["-C", repo, "check"]) == 0
    _git(repo, "commit", "-qm", "merge feat")
    assert _cli(["-C", repo, "check"]) == 0


def test_the_new_number_is_unused_on_either_side_and_the_trunk(repo):
    _collide(repo)
    _git(repo, "checkout", "-q", "main")
    for title in ("Main two", "Main three", "Main four"):    # main reaches REQ-0005
        _new(repo, title)
    _commit(repo, "main: more")
    _git(repo, "checkout", "-q", "feat")
    _merge(repo, "main")

    assert _cli(["-C", repo, "resolve"]) == 0
    assert "title: Feat item" in _item(repo, "REQ-0006")
    assert "title: Main two" in _item(repo, "REQ-0003")       # main's REQ-0003 kept
    assert "title: Feat dependent" in _item(repo, "REQ-0007")  # feat's moved too
    assert "target: REQ-0006" in _item(repo, "REQ-0007")


def test_dry_run_changes_nothing(repo, capsys):
    _collide(repo)
    _git(repo, "checkout", "-q", "feat")
    _merge(repo, "main")
    before = _git(repo, "status", "--porcelain").stdout

    assert _cli(["-C", repo, "resolve", "--dry-run"]) == 0
    assert "would move REQ-0002 -> REQ-0004" in capsys.readouterr().out
    assert _git(repo, "status", "--porcelain").stdout == before
    assert not (repo / "requirements" / "REQ-0004.yml").exists()


def test_a_file_both_sides_linked_from_is_refused_and_nothing_changes(repo, capsys):
    _git(repo, "checkout", "-q", "feat")
    _new(repo, "Feat item")
    assert _cli(["-C", repo, "link", "REQ-0001", "REQ-0002", "--type", "relates"]) == 0
    _commit(repo, "feat")
    _git(repo, "checkout", "-q", "main")
    _new(repo, "Main item")
    assert _cli(["-C", repo, "link", "REQ-0001", "REQ-0002", "--type", "relates"]) == 0
    _commit(repo, "main")
    _git(repo, "checkout", "-q", "feat")
    _merge(repo, "main")
    before = _git(repo, "status", "--porcelain").stdout

    assert _cli(["-C", repo, "resolve"]) == 2
    err = capsys.readouterr().err
    assert "REQ-0001.yml links to REQ-0002 on both sides" in err
    assert _git(repo, "status", "--porcelain").stdout == before


def test_no_merge_in_progress_is_refused(repo, capsys):
    assert _cli(["-C", repo, "resolve"]) == 2
    assert "no merge in progress" in capsys.readouterr().err


def test_a_merge_without_collisions_says_so(repo, capsys):
    _git(repo, "checkout", "-q", "feat")
    (repo / "notes.txt").write_text("feat\n")
    _commit(repo, "feat notes")
    _git(repo, "checkout", "-q", "main")
    (repo / "notes.txt").write_text("main\n")
    _commit(repo, "main notes")
    assert _git(repo, "merge", "feat", check=False).returncode != 0

    assert _cli(["-C", repo, "resolve"]) == 0
    out = capsys.readouterr().out
    assert "no UID collisions" in out and "notes.txt" in out


def test_neither_side_on_trunk_needs_keep(repo, capsys):
    _git(repo, "checkout", "-q", "-b", "other", "main")
    _new(repo, "Other item")
    _commit(repo, "other")
    _git(repo, "checkout", "-q", "feat")
    _new(repo, "Feat item")
    _commit(repo, "feat")
    _merge(repo, "other")

    assert _cli(["-C", repo, "resolve"]) == 2
    assert "--keep ours|theirs" in capsys.readouterr().err
    assert _cli(["-C", repo, "resolve", "--keep", "theirs"]) == 0
    assert "title: Other item" in _item(repo, "REQ-0002")
    assert "title: Feat item" in _item(repo, "REQ-0003")


def test_what_cannot_be_rewritten_is_reported(repo, capsys):
    _git(repo, "checkout", "-q", "feat")
    _new(repo, "Feat item")
    _new(repo, "Feat prose")
    assert _cli(["-C", repo, "amend", "REQ-0003", "--text",
                 "Builds on REQ-0002."]) == 0
    _commit(repo, "Implement REQ-0002")
    _git(repo, "checkout", "-q", "main")
    _new(repo, "Main item")
    _commit(repo, "main")
    _git(repo, "checkout", "-q", "feat")
    _merge(repo, "main")

    assert _cli(["-C", repo, "resolve"]) == 0
    out = capsys.readouterr().out
    assert "still mentions REQ-0002 in prose: text: Builds on REQ-0002." in out
    assert "(Implement REQ-0002) cites REQ-0002" in out
    assert "means what is now REQ-0004" in out
    assert "Builds on REQ-0002." in _item(repo, "REQ-0003")   # prose is left alone


def test_rewrite_leaves_the_ratification_record_alone():
    text = ("uid: SR-0002\ntype: system_requirement\nstatus: ratified\n"
            "title: T\ntext: Uses SR-0002.\nlinks:\n- target: SR-0009\n  type: relates\n"
            "- target: UR-0001\n  type: implements\nattrs:\n  ratified_links:\n"
            "  - type: relates\n    target: SR-0009\n")
    new, count = rewrite(text, {"SR-0009": "SR-0010"}, uid="SR-0003")
    assert count == 1
    assert new.startswith("uid: SR-0003\n")
    assert "- target: SR-0010\n  type: relates" in new
    assert "    target: SR-0009\n" in new                     # the signed record
    assert "text: Uses SR-0002." in new


def test_rewrite_refuses_a_layout_it_cannot_prove():
    text = "uid: SR-0002\ntype: t\nlinks: [{target: SR-0009, type: relates}]\n"
    with pytest.raises(CollisionError, match="layout"):
        rewrite(text, {"SR-0009": "SR-0010"})


def test_a_file_not_named_by_its_uid_is_an_error(repo):
    path = repo / "requirements" / "REQ-0001.yml"
    path.rename(path.with_name("REQ-0099.yml"))
    findings = [f for f in validate(load_project(repo)) if f.rule == "uid-filename"]
    assert [f.uid for f in findings] == ["REQ-0001"]
    assert "rename it to REQ-0001.yml" in findings[0].message


def test_the_suggested_check_does_not_mistake_the_kept_item_for_a_status_move(
        repo, capsys):
    _git(repo, "checkout", "-q", "feat")
    _new(repo, "Feat item")
    assert _cli(["-C", repo, "status", "REQ-0002", "approved"]) == 0
    _commit(repo, "feat")
    _git(repo, "checkout", "-q", "main")
    _new(repo, "Main item")                                  # still draft
    _commit(repo, "main")
    _git(repo, "checkout", "-q", "feat")
    _merge(repo, "main")

    assert _cli(["-C", repo, "resolve"]) == 0
    assert "--base MERGE_HEAD" in capsys.readouterr().out
    assert _cli(["-C", repo, "check", "--base", "MERGE_HEAD"]) == 0
