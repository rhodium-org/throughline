# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""`tl register type` declares the item type of a register that already exists
(SR-0349), and migration ends with findings while a register declares none that it
cannot type (SR-0350).

Found on 15 November 2026 shaping a graph with tl:shape: its registers were made
with no type, `tl new INT` was refused, and `tl migrate` types only a register the
Tool laid down or one whose items agree, so every empty register stayed untyped
and the one remedy was a hand edit of the manifest.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

from throughline.cli import main as cli_main
from throughline.storage import MANIFEST_NAME, load_project


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _init(root: Path) -> Path:
    assert _cli(["-C", root, "init", "--no-demo"]) == 0
    return root


def _manifest(root: Path, folder: str) -> dict:
    return yaml.safe_load((root / folder / MANIFEST_NAME).read_text())


def _old_register(root: Path, prefix: str, folder: str) -> None:
    """A register as one made before SR-0351 stands: declaring no item type."""
    assert _cli(["-C", root, "register", "new", prefix, folder,
                 "--type", "requirement"]) == 0
    path = root / folder / MANIFEST_NAME
    data = yaml.safe_load(path.read_text())
    data.pop("type")
    path.write_text(yaml.safe_dump(data))


def _new(root: Path, prefix: str, *extra) -> int:
    return _cli(["-C", root, "new", prefix, "--title", "t", "--origin", "ai",
                 "--no-interactive", *extra])


# ── SR-0349 ──────────────────────────────────────────────────────────────────

def test_declares_the_type_of_an_empty_register_and_new_uses_it(tmp_path, capsys):
    root = _init(tmp_path)
    _old_register(root, "RISK", "risks")
    assert _cli(["-C", root, "register", "type", "RISK", "requirement"]) == 0
    assert "now holds requirement" in capsys.readouterr().out
    assert _manifest(root, "risks")["type"] == "requirement"
    assert _new(root, "RISK") == 0
    assert load_project(root).get("RISK-0001").type == "requirement"


def test_replaces_a_declared_type_and_names_the_items_that_differ(tmp_path, capsys):
    root = _init(tmp_path)
    assert _new(root, "TEST") == 0                       # a test, as TEST holds
    assert _cli(["-C", root, "register", "type", "TEST", "requirement"]) == 0
    out = capsys.readouterr().out
    assert "now holds requirement (it held test)" in out
    assert "1 item(s) there are of another type" in out and "TEST-0001 (test)" in out
    assert _manifest(root, "tests")["type"] == "requirement"


def test_declaring_the_type_it_holds_writes_nothing(tmp_path, capsys):
    root = _init(tmp_path)
    manifest = root / "tests" / MANIFEST_NAME
    before = manifest.read_bytes()
    assert _cli(["-C", root, "register", "type", "TEST", "test"]) == 0
    assert "already holds test — nothing changed" in capsys.readouterr().out
    assert manifest.read_bytes() == before


def test_refuses_a_prefix_no_register_owns(tmp_path, capsys):
    root = _init(tmp_path)
    assert _cli(["-C", root, "register", "type", "NOPE", "test"]) == 2
    assert "no register with prefix 'NOPE'" in capsys.readouterr().err


def test_refuses_a_type_nothing_declares_or_holds(tmp_path, capsys):
    root = _init(tmp_path)
    _old_register(root, "RISK", "risks")
    capsys.readouterr()
    assert _cli(["-C", root, "register", "type", "RISK", "requirment"]) == 2
    err = capsys.readouterr().err
    assert "'requirment' is not an item type" in err
    assert "tl schema type add requirment" in err
    assert "type" not in _manifest(root, "risks")


def test_accepts_a_type_an_item_holds_though_none_declares_it(tmp_path):
    """Older graphs use types such as intent and test without declaring them."""
    root = _init(tmp_path)
    for prefix, folder in (("GAD", "gadgets"), ("EMP", "empty")):
        _old_register(root, prefix, folder)
    assert _new(root, "GAD", "--type", "gadget") == 0
    assert "gadget" not in load_project(root).schema.types
    assert _cli(["-C", root, "register", "type", "EMP", "gadget"]) == 0
    assert _manifest(root, "empty")["type"] == "gadget"


def test_the_refusal_of_new_names_the_command(tmp_path, capsys):
    root = _init(tmp_path)
    _old_register(root, "RISK", "risks")
    capsys.readouterr()
    assert _new(root, "RISK") != 0
    err = capsys.readouterr().err
    assert "declares no item type" in err
    assert "tl register type RISK <type>" in err
    assert ".register.yml" not in err


# ── SR-0351 ──────────────────────────────────────────────────────────────────

def test_register_new_refuses_without_a_type_and_writes_nothing(tmp_path, capsys):
    root = _init(tmp_path)
    assert _cli(["-C", root, "register", "new", "RISK", "risks"]) == 2
    err = capsys.readouterr().err
    assert "a register is made with the item type it holds" in err
    assert "--type" in err and "requirement" in err       # the types it may take
    assert not (root / "risks").exists()


def test_register_new_refuses_a_type_nothing_declares_or_holds(tmp_path, capsys):
    root = _init(tmp_path)
    assert _cli(["-C", root, "register", "new", "RISK", "risks", "--type", "risk"]) == 2
    assert "tl schema type add risk" in capsys.readouterr().err
    assert not (root / "risks").exists()
    assert _cli(["-C", root, "schema", "type", "add", "risk", "--because", "t"]) == 0
    assert _cli(["-C", root, "register", "new", "RISK", "risks", "--type", "risk"]) == 0
    assert _manifest(root, "risks")["type"] == "risk"


def test_register_new_takes_a_type_an_item_holds_or_another_register_holds(tmp_path):
    root = _init(tmp_path)
    assert _new(root, "REQ", "--type", "gadget") == 0       # held, never declared
    assert _cli(["-C", root, "register", "new", "GAD", "gadgets",
                 "--type", "gadget"]) == 0
    # a type held by two registers is allowed: some graphs do it on purpose
    assert _cli(["-C", root, "register", "new", "PRI", "principles",
                 "--type", "requirement"]) == 0
    assert _manifest(root, "principles")["type"] == "requirement"
    assert _manifest(root, "requirements")["type"] == "requirement"


# ── SR-0350 ──────────────────────────────────────────────────────────────────

def _drop_type(root: Path, folder: str) -> None:
    path = root / folder / MANIFEST_NAME
    data = yaml.safe_load(path.read_text())
    data.pop("type", None)
    path.write_text(yaml.safe_dump(data))


def test_migrate_ends_with_findings_naming_each_untyped_register(tmp_path, capsys):
    root = _init(tmp_path)
    _old_register(root, "EMP", "empty")
    _old_register(root, "MIX", "mixed")
    assert _new(root, "MIX", "--type", "requirement") == 0
    assert _new(root, "MIX", "--type", "test") == 0
    _drop_type(root, "tests")                            # a repair migration makes
    capsys.readouterr()

    assert _cli(["-C", root, "migrate"]) == 1
    out = capsys.readouterr().out
    assert "TEST = test" in out                          # the repair is reported…
    assert _manifest(root, "tests")["type"] == "test"    # …and kept
    assert "2 register(s) declare no item type" in out
    assert re.search(rf"tl -C {re.escape(str(root))} register type EMP <type>\s+empty/", out)
    assert re.search(rf"tl -C {re.escape(str(root))} register type MIX <type>\s+mixed/", out)

    assert _cli(["-C", root, "register", "type", "EMP", "requirement"]) == 0
    assert _cli(["-C", root, "register", "type", "MIX", "requirement"]) == 0
    capsys.readouterr()
    assert _cli(["-C", root, "migrate"]) == 0
    assert "declare no item type" not in capsys.readouterr().out


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                   check=True)


def test_migrate_beneath_names_each_graph_holding_one(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "grace@example.com")
    _git(repo, "config", "user.name", "Grace")
    typed = _init(repo / "typed")
    untyped = _init(repo / "untyped")
    _old_register(untyped, "EMP", "empty")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "baseline")
    capsys.readouterr()

    for apply in ([], ["--apply"]):
        assert _cli(["migrate", "--beneath", repo, *apply]) == 1
        out = capsys.readouterr().out
        assert "1 graph(s) hold a register that declares no item type" in out
        assert f"{untyped.resolve()}  EMP" in out
        assert str(typed.resolve()) + "  " not in out

    assert _cli(["-C", untyped, "register", "type", "EMP", "requirement"]) == 0
    capsys.readouterr()
    assert _cli(["migrate", "--beneath", repo]) == 0


def test_a_project_declaring_no_types_takes_any_name(tmp_path):
    """Item types are a closed list only where the project declares them."""
    root = tmp_path / "p"
    root.mkdir()
    (root / "throughline.toml").write_text(
        '[project]\nname = "open"\nformat_version = 4\n'
        '[grounding]\nroot_types = ["intent"]\ndelivery_roots = ["intent"]\n'
        'ground_link_types = ["implements"]\n[links]\ntypes = ["implements"]\n'
        '[status]\nvalues = ["proposed", "ratified", "rejected", "deleted"]\n'
        '[status.roles]\ninitial = "proposed"\nproposed = "proposed"\n'
        'ratified = "ratified"\ninvalidated = "rejected"\ntombstone = "deleted"\n')
    assert not load_project(root).schema.types
    assert _cli(["-C", root, "register", "new", "INT", "vision", "--type", "intent"]) == 0
    assert _cli(["-C", root, "register", "type", "INT", "anything"]) == 0
    assert _cli(["-C", root, "register", "new", "X", "x"]) == 2   # still never untyped
