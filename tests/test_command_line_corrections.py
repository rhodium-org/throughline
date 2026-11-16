# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Corrections to what the command line says and does, found by writing its
specification and two other builds of it (16 November 2026): SR-0358, SR-0359,
SR-0361 to SR-0363, SR-0365 to SR-0367, and SR-0100, SR-0120 and SR-0161 as they
already stood.
"""
from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path

import pytest

import throughline.cli as climod
from throughline.cli import main as cli_main
from throughline.dump import build_dump
from throughline.fingerprint import fingerprint
from throughline.storage import load_project


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _demo(root: Path) -> Path:
    assert _cli(["-C", root, "init"]) == 0
    for title in ("Two", "Three"):
        assert _cli(["-C", root, "new", "REQ", "--title", title, "--text", "t.",
                     "--ground", "INT-0001", "--no-interactive"]) == 0
    return root


def _composed(tmp_path: Path) -> Path:
    """``app`` borrows ``lib``'s items; its own REQ-0001 to REQ-0003 ground in
    the source's intent, and REQ-0003 rests on REQ-0002."""
    lib, app = tmp_path / "lib", tmp_path / "app"
    assert _cli(["-C", lib, "init", "--name", "lib"]) == 0
    assert _cli(["-C", lib, "ratify", "INT-0001", "REQ-0001", "--by", "Ada"]) == 0
    assert _cli(["-C", app, "init", "--name", "app", "--no-demo"]) == 0
    with (app / "throughline.toml").open("a", encoding="utf-8") as f:
        f.write('\n[[sources]]\nnamespace = "lib"\npath = "../lib"\n')
    for title, ground in (("One", "lib:INT-0001"), ("Two", "lib:INT-0001"),
                          ("Three", "REQ-0002")):
        assert _cli(["-C", app, "new", "REQ", "--title", title, "--text", "t.",
                     "--ground", ground, "--no-interactive"]) == 0
    return app


def _options(command: str) -> set[str]:
    parser = climod._extend_parser(climod.build_parser())
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return {o for a in sub.choices[command]._actions for o in a.option_strings}


# --------------------------------------------------------- what tl says of itself

@pytest.mark.parametrize("flags", [[], ["--no-demo"], ["--no-defaults"], ["--bare"]])
def test_init_names_only_options_it_has(tmp_path, capsys, flags):
    """SR-0100: `--demo` and `--defaults` were advised and never existed."""
    assert _cli(["-C", tmp_path / "p", "init", *flags]) == 0
    said = capsys.readouterr().out
    named = set(re.findall(r"(?<![\w-])--[a-z][a-z-]*", said))
    assert named <= _options("init"), named - _options("init")


def test_the_brief_documents_init_with_options_it_has(tmp_path, capsys):
    """SR-0161."""
    assert _cli(["-C", tmp_path / "p", "init"]) == 0
    capsys.readouterr()
    assert _cli(["-C", tmp_path / "p", "context"]) == 0
    (line,) = [ln for ln in capsys.readouterr().out.splitlines()
               if ln.startswith("tl init ")]
    named = set(re.findall(r"(?<![\w-])--[a-z][a-z-]*", line))
    assert named and named <= _options("init"), named - _options("init")


def test_help_describes_the_program_not_its_parser(capsys):
    """SR-0362."""
    with pytest.raises(SystemExit):
        _cli(["--help"])
    out = capsys.readouterr().out
    assert "throughline: requirements kept in Git" in " ".join(out.split())
    assert "The shape of the command line" not in out


def test_diff_outside_a_repository_is_refused_as_a_comparison(tmp_path, capsys):
    """SR-0361: the refusal was worded for `tl docs --at`."""
    root = _demo(tmp_path / "p")
    capsys.readouterr()
    assert _cli(["-C", root, "diff"]) == 2
    err = capsys.readouterr().err
    assert "is not inside a git work tree" in err and "`tl diff` compares" in err
    assert "render" not in err and "--at" not in err
    assert _cli(["-C", root, "docs", "--at", "HEAD"]) == 2
    assert "cannot render `--at HEAD`" in capsys.readouterr().err


def test_init_writes_any_name_as_toml(tmp_path, capsys):
    """SR-0367."""
    name = 'A "quoted" \\ name\twith a tab\nand a line'
    root = tmp_path / "p"
    assert _cli(["-C", root, "init", "--name", name]) == 0
    config = tomllib.loads((root / "throughline.toml").read_text(encoding="utf-8"))
    assert config["project"]["name"] == name
    assert _cli(["-C", root, "check"]) == 0


# -------------------------------------------------------------------- the dump

@pytest.mark.parametrize("composed", [False, True], ids=["alone", "composed"])
def test_dump_to_a_file_that_cannot_be_written_is_refused(tmp_path, capsys, composed):
    """SR-0358."""
    root = _composed(tmp_path) if composed else _demo(tmp_path / "p")
    capsys.readouterr()
    target = tmp_path / "no-such-folder" / "dump.json"
    assert _cli(["-C", root, "dump", "-o", target]) == 2
    got = capsys.readouterr()
    assert f"cannot write {target}" in got.err and got.err.count("\n") == 1
    assert got.out == "" and not target.exists()


# ------------------------------------------------------------------ the picker

def _terminal(monkeypatch, answers):
    """A terminal whose reader gives ``answers`` in turn. The prompt each
    `input()` was handed is kept: it is what would have gone to stdout."""
    given = []
    replies = iter(answers)

    def _input(prompt=""):
        given.append(prompt)
        return next(replies)
    monkeypatch.setattr(climod, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", _input)
    return given


def test_the_picker_asks_on_stderr(tmp_path, monkeypatch, capsys):
    """SR-0120: the question went to stdout while its lists went to stderr."""
    root = _demo(tmp_path / "p")
    capsys.readouterr()
    given = _terminal(monkeypatch, ["c"])
    assert _cli(["-C", root, "delete", "--reason", "gone"]) == 2
    got = capsys.readouterr()
    assert given == [""]                               # nothing handed to stdout
    assert got.out == ""
    assert "choose a type:" in got.err and "type number, or [c]ancel [c]: " in got.err


def test_a_confirmation_and_a_value_are_asked_on_stderr(tmp_path, monkeypatch, capsys):
    root = _demo(tmp_path / "p")
    capsys.readouterr()
    given = _terminal(monkeypatch, ["Ada", "y"])
    assert _cli(["-C", root, "ratify", "REQ-0001"]) == 0
    got = capsys.readouterr()
    assert given == ["", ""]
    assert got.out == "REQ-0001 ratified by Ada\n"
    assert "ratifier" in got.err and "ratify REQ-0001 as Ada? [y/N]: " in got.err


def test_restore_offers_the_proposed_deletions(tmp_path, monkeypatch, capsys):
    """SR-0359: the picker filtered tombstones out, then kept only tombstones."""
    root = _demo(tmp_path / "p")
    assert _cli(["-C", root, "ratify", "REQ-0001", "REQ-0002", "--by", "Ada"]) == 0
    assert _cli(["-C", root, "delete", "REQ-0001", "--reason", "gone"]) == 0
    assert _cli(["-C", root, "delete", "REQ-0002", "--reason", "gone"]) == 0
    assert _cli(["-C", root, "ratify", "REQ-0002", "--by", "Ada"]) == 0   # stands
    capsys.readouterr()
    _terminal(monkeypatch, ["1"])
    assert _cli(["-C", root, "restore"]) == 0
    got = capsys.readouterr()
    assert "REQ-0001  [deleted]" in got.err
    assert "REQ-0002" not in got.err and "REQ-0003" not in got.err
    assert got.out.startswith("restored REQ-0001 to ratified")


def test_no_other_picker_offers_a_tombstone(tmp_path, monkeypatch, capsys):
    root = _demo(tmp_path / "p")
    assert _cli(["-C", root, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    assert _cli(["-C", root, "delete", "REQ-0001", "--reason", "gone"]) == 0
    capsys.readouterr()
    _terminal(monkeypatch, ["c"])
    assert _cli(["-C", root, "invalidate"]) == 2
    assert "requirement  (2)" in capsys.readouterr().err


# ------------------------------------------------------- a project with sources

def test_ratifying_several_passes_over_one_already_signed_with_sources_too(tmp_path, capsys):
    """SR-0363: with sources, one item already signed refused the whole run."""
    app = _composed(tmp_path)
    assert _cli(["-C", app, "ratify", "REQ-0001", "--by", "Ada"]) == 0
    capsys.readouterr()
    assert _cli(["-C", app, "ratify", "REQ-0001", "REQ-0002", "--by", "Ada"]) == 0
    got = capsys.readouterr()
    assert "REQ-0001 is already signed by Ada" in got.err
    assert "left as it is" in got.err
    assert got.out == "REQ-0002 ratified by Ada\n"
    assert _cli(["-C", app, "ratify", "REQ-0001", "REQ-0002", "--by", "Ada"]) == 0
    assert "nothing was signed" in capsys.readouterr().err
    # Alone, an item already signed is still refused, as it is without sources.
    assert _cli(["-C", app, "ratify", "REQ-0001", "--by", "Ada"]) == 2


def test_a_deletion_with_dependents_is_ratified_with_sources_too(tmp_path, capsys):
    """SR-0363: it was refused with a message written for a library's caller."""
    app = _composed(tmp_path)
    assert _cli(["-C", app, "ratify", "REQ-0002", "REQ-0003", "--by", "Ada"]) == 0
    assert _cli(["-C", app, "delete", "REQ-0002", "--reason", "gone"]) == 0
    capsys.readouterr()
    assert _cli(["-C", app, "ratify", "REQ-0002", "--by", "Ada"]) == 0
    got = capsys.readouterr()
    assert "cascade" not in got.err
    assert "REQ-0002 deletion ratified by Ada — retired; 1 dependent(s) marked suspect" \
        in got.out and "  REQ-0003" in got.out
    project = load_project(app)
    assert project.get("REQ-0003").status == "suspect"
    assert project.get("REQ-0003").attrs["suspect_reasons"] == ["upstream REQ-0002 deleted"]


# ------------------------------------------------------------------ change sets

def _apply(root: Path, operations, capsys) -> dict:
    path = root.parent / "set.json"
    path.write_text(json.dumps({"change_set_format": 2, "operations": operations}),
                    encoding="utf-8")
    capsys.readouterr()
    assert _cli(["-C", root, "apply", path, "--format", "json"]) == 0
    return json.loads(capsys.readouterr().out)


def _rev(root: Path, uid: str) -> str:
    dump = build_dump(load_project(root), "9.9.9")
    return next(i["revision"] for i in dump["items"] if i["uid"] == uid)


def test_a_change_set_stamps_a_stale_link_again(tmp_path, capsys):
    """SR-0365: a link already there was "already applied", stale stamp and all."""
    root = _demo(tmp_path / "p")
    assert _cli(["-C", root, "link", "REQ-0002", "REQ-0003", "--type", "relates",
                 "--stamp"]) == 0
    assert _cli(["-C", root, "amend", "REQ-0003", "--text", "Changed."]) == 0

    def link():
        return {"op": "link", "uid": "REQ-0002", "revision": _rev(root, "REQ-0002"),
                "target": "REQ-0003", "type": "relates", "stamp": True}

    def stamp():
        project = load_project(root)
        (held,) = [l.stamp for l in project.get("REQ-0002").links if l.target == "REQ-0003"]
        return held, fingerprint(project.get("REQ-0003"), project.schema)

    held, now = stamp()
    assert held != now
    report = _apply(root, [link()], capsys)
    assert [o["outcome"] for o in report["operations"]] == ["restamped"]
    held, now = stamp()
    assert held == now
    report = _apply(root, [link()], capsys)
    assert [o["outcome"] for o in report["operations"]] == ["already applied"]
    # A link that asks for no stamp is already there, as before.
    unstamped = dict(link(), stamp=False)
    report = _apply(root, [unstamped], capsys)
    assert [o["outcome"] for o in report["operations"]] == ["already applied"]


# -------------------------------------------------------------------- migration

def test_migrate_gives_its_advice_and_makes_the_unions_pass_with_a_register_untyped(
        tmp_path, capsys):
    """SR-0366: a register left untyped is a finding, and ended the migration of
    a project with sources before the union's pass and the advice."""
    app = _composed(tmp_path)
    old = app / "old"
    old.mkdir()
    (old / ".register.yml").write_text("prefix: OLD\ndigits: 4\n", encoding="utf-8")
    capsys.readouterr()
    assert _cli(["-C", app, "migrate"]) == 1
    out = capsys.readouterr().out
    assert "1 register(s) declare no item type" in out
    assert "await a person's signature" in out            # the advice (SR-0317)
