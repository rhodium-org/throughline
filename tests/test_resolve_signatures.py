# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A merge conflict confined to an item's signatures is settled by keeping every
signature (SR-0265).

Two people sign the same item on two branches. Each adds an entry to its
signature list, git cannot merge the two additions, and `tl resolve` knew only
the conflict of one UID added twice: it left the file "still in conflict" for
someone to settle by hand, where picking a side loses a signature.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from throughline.cli import main as cli_main
from throughline.storage import load_project
from throughline.validate import validate


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, check=check)


def _commit(root: Path, msg: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", msg)


def _file(root: Path) -> Path:
    return root / "requirements" / "REQ-0001.yml"


@pytest.fixture
def repo(tmp_path) -> Path:
    """main holds INT-0001 and REQ-0001, unsigned; `feat` branches off it."""
    root = tmp_path / "repo"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "Why",
                 "--no-interactive"]) == 0
    assert _cli(["-C", root, "new", "REQ", "--title", "Base", "--text", "t.",
                 "--ground", "INT-0001", "--no-interactive"]) == 0
    _commit(root, "base")
    _git(root, "branch", "feat")
    return root


def _sign(root: Path, branch: str, by: str, ident: str) -> None:
    _git(root, "checkout", "-q", branch)
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", by, "--by-id", ident]) == 0
    _commit(root, f"{by} signs REQ-0001")


def _both_sign(root: Path) -> None:
    _sign(root, "feat", "Bob Byte", "github:bob")
    _sign(root, "main", "Ada Lovelace", "github:ada")
    done = _git(root, "merge", "feat", check=False)
    assert done.returncode != 0 and "CONFLICT (content)" in done.stdout
    assert "<<<<<<<" in _file(root).read_text(encoding="utf-8")


def test_both_signatures_are_kept_and_the_merge_can_be_finished(repo, capsys):
    """SR-0265: every entry from both sides is kept, and it says which."""
    _both_sign(repo)
    capsys.readouterr()

    assert _cli(["-C", repo, "resolve"]) == 0

    said = capsys.readouterr().out
    assert "REQ-0001" in said and "Ada Lovelace" in said and "Bob Byte" in said
    text = _file(repo).read_text(encoding="utf-8")
    assert "<<<<<<<" not in text
    signed = [e["id"] for e in yaml.safe_load(text)["ratifications"]]
    assert sorted(signed) == ["github:ada", "github:bob"]
    assert _git(repo, "diff", "--name-only", "--diff-filter=U").stdout == ""
    _git(repo, "commit", "-qm", "merge feat")
    project = load_project(repo)
    assert not [f for f in validate(project) if f.severity == "error"]
    assert len(project.get("REQ-0001").ratifications) == 2


def test_the_kept_signatures_are_oldest_first(repo):
    """SR-0254: the list is one signature per person, oldest first."""
    _both_sign(repo)
    assert _cli(["-C", repo, "resolve"]) == 0
    entries = yaml.safe_load(_file(repo).read_text(encoding="utf-8"))["ratifications"]
    assert [e["at"] for e in entries] == sorted(e["at"] for e in entries)


def test_a_conflict_that_reaches_another_field_is_refused(repo, capsys):
    """SR-0265: it settles a conflict confined to the signature list, and refuses
    one that reaches any other field."""
    _git(repo, "checkout", "-q", "feat")
    assert _cli(["-C", repo, "amend", "REQ-0001", "--text", "Feat says this."]) == 0
    _commit(repo, "feat rewords")
    _sign(repo, "feat", "Bob Byte", "github:bob")
    _git(repo, "checkout", "-q", "main")
    assert _cli(["-C", repo, "amend", "REQ-0001", "--text", "Main says that."]) == 0
    _commit(repo, "main rewords")
    _sign(repo, "main", "Ada Lovelace", "github:ada")
    assert _git(repo, "merge", "feat", check=False).returncode != 0
    before = _file(repo).read_text(encoding="utf-8")
    capsys.readouterr()

    assert _cli(["-C", repo, "resolve"]) == 0

    said = capsys.readouterr().out
    assert "REQ-0001" in said and "more than its signatures" in said
    assert _file(repo).read_text(encoding="utf-8") == before
    assert "REQ-0001.yml" in _git(repo, "diff", "--name-only",
                                  "--diff-filter=U").stdout


def test_one_person_signing_on_both_sides_is_kept_once(repo):
    """SR-0265: one per signer identifier. The later signature is the one kept."""
    _sign(repo, "feat", "Ada L.", "github:ada")
    _sign(repo, "main", "Ada Lovelace", "github:ada")
    assert _git(repo, "merge", "feat", check=False).returncode != 0
    assert _cli(["-C", repo, "resolve"]) == 0
    entries = yaml.safe_load(_file(repo).read_text(encoding="utf-8"))["ratifications"]
    assert [e["id"] for e in entries] == ["github:ada"]
    assert entries[0]["by"] == "Ada Lovelace"


def test_a_dry_run_says_what_it_would_keep_and_changes_nothing(repo, capsys):
    _both_sign(repo)
    before = _file(repo).read_text(encoding="utf-8")
    capsys.readouterr()
    assert _cli(["-C", repo, "resolve", "--dry-run"]) == 0
    assert "would keep" in capsys.readouterr().out
    assert _file(repo).read_text(encoding="utf-8") == before
