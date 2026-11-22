# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""`tl resolve` refuses what it cannot read, and keeps a file's line endings
(SR-0357, SR-0364).

Each scenario is a real merge stopped on a real add/add conflict, with one file
of the side that moves damaged before the merge.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from throughline.cli import main as cli_main


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), "-c", "core.autocrlf=false", *args],
                          capture_output=True, check=check)


def _new(root: Path, title: str, *, ground: str = "INT-0001") -> None:
    assert _cli(["-C", root, "new", "REQ", "--title", title, "--text", "t.",
                 "--ground", ground, "--no-interactive"]) == 0


def _collide(tmp_path: Path, damage=None) -> Path:
    """main and feat both add REQ-0002; feat also adds REQ-0003 linking to its
    own, and is damaged by ``damage(root)`` before it is committed. Merging feat
    into main stops on the conflict."""
    root = tmp_path / "repo"
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    assert _cli(["-C", root, "new", "INT", "--type", "intent", "--title", "Why",
                 "--no-interactive"]) == 0
    _new(root, "Base")
    _git(root, "add", "-A"), _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-q", "-b", "feat")
    _new(root, "Feat item")
    _new(root, "Feat dependent", ground="REQ-0002")
    if damage is not None:
        damage(root)
    _git(root, "add", "-A"), _git(root, "commit", "-qm", "feat")
    _git(root, "checkout", "-q", "main")
    _new(root, "Main item")
    _git(root, "add", "-A"), _git(root, "commit", "-qm", "main")
    done = _git(root, "merge", "feat", check=False)
    assert done.returncode != 0 and b"CONFLICT (add/add)" in done.stdout
    return root


def _state(root: Path):
    files = {p.relative_to(root).as_posix(): p.read_bytes()
             for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts}
    return files, _git(root, "ls-files", "--stage").stdout


def _manifest(text: str):
    def damage(root: Path) -> None:
        (root / "requirements" / ".register.yml").write_text(text, encoding="utf-8")
    return damage


def _append(name: str, body: bytes):
    def damage(root: Path) -> None:
        with (root / "requirements" / name).open("ab") as f:
            f.write(body)
    return damage


@pytest.mark.parametrize("damage,says", [
    (_manifest("prefix: REQ\ndigits: many\n"), "'digits' is 'many'"),
    (_manifest("prefix: REQ\ndigits: -3\n"), "'digits' is -3"),
    (_manifest("prefix: REQ\ndigits: [4]\n"), "'digits' is [4]"),
    (_manifest("prefix: REQ\nreserved: 7\n"), "'reserved' is 7"),
    (_manifest("prefix: REQ\nreserved: [one]\n"), "'reserved' is ['one']"),
    (_append("REQ-0003.yml", b"note: caf\xe9\n"), "REQ-0003.yml (at MERGE_HEAD) is not UTF-8"),
    (_append("REQ-0002.yml", b"note: caf\xe9\n"), "REQ-0002.yml (theirs) is not UTF-8"),
    (_append("REQ-0003.yml", b"created: 2026-13-45\n"), "month must be in 1..12"),
    # CPython's own words for the bad date, which 3.14 changed.
    (_append("REQ-0002.yml", b"created: 2026-02-30\n"),
     ("day is out of range", "day 30 must be in range")),
], ids=["digits-words", "digits-below-nought", "digits-list", "reserved-number",
        "reserved-words", "not-utf8-dependent", "not-utf8-collided", "no-such-month",
        "no-such-day"])
@pytest.mark.parametrize("dry", [["--dry-run"], []], ids=["dry-run", "for-real"])
def test_resolve_refuses_what_it_cannot_read_and_writes_nothing(
        tmp_path, capsys, damage, says, dry):
    root = _collide(tmp_path, damage)
    before = _state(root)
    capsys.readouterr()
    assert _cli(["-C", root, "resolve", *dry]) == 2
    got = capsys.readouterr()
    words = says if isinstance(says, tuple) else (says,)
    assert any(w in got.err for w in words)
    assert got.err.count("\n") == 1 and got.out == ""
    assert _state(root) == before


def test_resolve_reads_prose_that_is_not_utf8_without_stopping(tmp_path, capsys):
    """Prose is reported, never rewritten, so a file in another encoding beside
    the graph is no reason to refuse the resolution."""
    def damage(root: Path) -> None:
        (root / "notes.txt").write_bytes(b"caf\xe9 mentions REQ-0002 here\n")
    root = _collide(tmp_path, damage)
    assert _cli(["-C", root, "resolve"]) == 0
    out = capsys.readouterr().out
    assert "move REQ-0002 -> REQ-0004" in out
    assert "notes.txt still mentions REQ-0002 in prose" in out
    assert (root / "notes.txt").read_bytes() == b"caf\xe9 mentions REQ-0002 here\n"


def test_resolve_keeps_the_line_endings_of_the_files_it_rewrites(tmp_path):
    """SR-0364: a renumbered item and an item whose link follows it keep CRLF."""
    def damage(root: Path) -> None:
        for name in ("REQ-0002.yml", "REQ-0003.yml"):
            path = root / "requirements" / name
            body = path.read_bytes().replace(b"\r\n", b"\n")
            path.write_bytes(body.replace(b"\n", b"\r\n"))
    root = _collide(tmp_path, damage)
    assert _cli(["-C", root, "resolve"]) == 0
    moved = (root / "requirements" / "REQ-0004.yml").read_bytes()
    relinked = (root / "requirements" / "REQ-0003.yml").read_bytes()
    for body in (moved, relinked):
        assert b"\r\n" in body and b"\n" not in body.replace(b"\r\n", b"")
    assert moved.startswith(b"uid: REQ-0004\r\n")
    assert b"target: REQ-0004\r\n" in relinked
    assert _cli(["-C", root, "check"]) == 0


def test_resolve_writes_every_file_or_none(tmp_path, monkeypatch, capsys):
    """SR-0356: a file that cannot be written leaves the merge as it stopped."""
    import os

    import throughline.atomic as atomic
    root = _collide(tmp_path)
    before = _state(root)
    real, seen = os.replace, []

    def replace(src, dst):
        seen.append(dst)
        if len(seen) == 3:
            raise OSError(28, "No space left on device", str(dst))
        return real(src, dst)
    monkeypatch.setattr(atomic.os, "replace", replace)
    assert _cli(["-C", root, "resolve"]) == 2
    assert "nothing was written" in capsys.readouterr().err
    assert _state(root) == before
