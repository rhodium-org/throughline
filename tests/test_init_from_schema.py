# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A graph that follows a standard is started with one command (UR-0043, SR-0283,
SR-0284).

Starting a plan against the plan standard took `tl init --bare`, a hand-written
`[[sources]]` block and one `tl register new` per kind, each prefix and folder
copied from the standard's example. These tests hold `tl init --from-schema` to
its requirements: it writes the one schema source and nothing of tl's own
defaults, and resolves the source before writing anything (SR-0283); and a
standard may declare the registers its followers start with, which become the
follower's own (SR-0284).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from throughline.cli import main as cli_main
from throughline.storage import ProjectError, load_project
from throughline.validate import validate

STANDARD = """
[types.slide]
attrs.layout = { type = "enum", values = ["cards", "stats"], required = true }
"""


def _cli(argv) -> int:
    return cli_main([str(a) for a in argv])


def _append(root: Path, text: str) -> None:
    cfg = root / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + text, encoding="utf-8")


def _standard(tmp_path: Path, name: str = "std", extra: str = STANDARD) -> Path:
    std = tmp_path / name
    assert _cli(["-C", std, "init", "--no-demo"]) == 0
    _append(std, extra)
    return std


def _errors(root: Path) -> list:
    # A standard here declares in its configuration, not by register items, so the
    # new graph starts with no registers and is empty until one is made (SR-0296).
    return [f for f in validate(load_project(root))
            if f.severity == "error" and f.rule != "empty-graph"]


# --------------------------------------------------------------------------- #
# SR-0283 — init --from-schema writes the schema source, resolved first
# --------------------------------------------------------------------------- #

def test_the_new_graph_holds_the_schema_source_and_none_of_the_defaults(tmp_path, monkeypatch):
    _standard(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "std", "--name", "Deck"]) == 0
    text = (tmp_path / "fol" / "throughline.toml").read_text(encoding="utf-8")
    assert 'path = "../std"' in text and "schema = true" in text
    # Nothing of tl's own starter schema: the standard is the schema.
    assert "[types." not in text and "[grounding]" not in text
    project = load_project(tmp_path / "fol")
    assert not project.registers and not list(project.items())
    assert project.schema.attr("slide", "layout").values == ("cards", "stats")
    assert not _errors(tmp_path / "fol")


def test_the_path_is_written_relative_to_the_new_graph(tmp_path, monkeypatch):
    _standard(tmp_path, "standards/deck")
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "graphs/fol", "init", "--from-schema", "standards/deck"]) == 0
    text = (tmp_path / "graphs/fol/throughline.toml").read_text(encoding="utf-8")
    assert 'path = "../../standards/deck"' in text
    assert not _errors(tmp_path / "graphs/fol")


def test_a_source_that_does_not_exist_leaves_no_project_behind(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "nowhere"]) == 2
    assert "nowhere" in capsys.readouterr().err
    assert not (tmp_path / "fol").exists()


def test_a_folder_that_is_not_a_graph_leaves_no_project_behind(tmp_path, monkeypatch):
    (tmp_path / "plain").mkdir()
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "plain"]) == 2
    assert not (tmp_path / "fol").exists()


def test_a_folder_that_already_existed_is_left_where_it_was(tmp_path, monkeypatch):
    (tmp_path / "fol").mkdir()
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "nowhere"]) == 2
    assert (tmp_path / "fol").is_dir() and not any((tmp_path / "fol").iterdir())


def test_a_url_needs_a_ref_and_nothing_is_fetched_without_one(tmp_path, capsys):
    url = "https://example.invalid/throughline-plan-standard"
    assert _cli(["-C", tmp_path / "fol", "init", "--from-schema", url]) == 2
    assert "ref" in capsys.readouterr().err
    assert not (tmp_path / "fol").exists()


def test_a_ref_pins_only_a_url(tmp_path, monkeypatch, capsys):
    _standard(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "std", "--ref", "v1"]) == 2
    assert "--ref" in capsys.readouterr().err
    assert not (tmp_path / "fol").exists()


def test_a_ref_or_namespace_without_from_schema_is_refused(tmp_path):
    assert _cli(["-C", tmp_path / "a", "init", "--ref", "v1"]) == 2
    assert _cli(["-C", tmp_path / "b", "init", "--namespace", "plan"]) == 2
    assert not (tmp_path / "a").exists() and not (tmp_path / "b").exists()


@pytest.mark.parametrize("folder, expected", [
    ("deck", "deck"),
    ("Deck", "deck"),
    ("deck-standard", "deck-standard"),
])
def test_without_a_suggestion_the_namespace_is_the_last_part_as_written(
        tmp_path, monkeypatch, folder, expected):
    _standard(tmp_path, folder)
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", folder]) == 0
    text = (tmp_path / "fol" / "throughline.toml").read_text(encoding="utf-8")
    assert f'namespace = "{expected}"' in text


def test_a_name_too_long_to_compose_is_refused_not_shortened(tmp_path, monkeypatch, capsys):
    std = _standard(tmp_path, "throughline-deck-standard")
    assert _cli(["-C", std, "register", "new", "SLD", "slides", "--type", "slide"]) == 0
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "throughline-deck-standard"]) == 2
    assert "--namespace" in capsys.readouterr().err
    assert not (tmp_path / "fol").exists()


def _suggest(std: Path, namespace: str) -> None:
    cfg = std / "throughline.toml"
    text = cfg.read_text(encoding="utf-8")
    cfg.write_text(text.replace("[project]\n",
                                f'[project]\nsuggested_namespace = "{namespace}"\n', 1),
                   encoding="utf-8")


def test_the_standard_suggests_its_own_namespace(tmp_path, monkeypatch):
    std = _standard(tmp_path, "throughline-deck-standard")
    assert _cli(["-C", std, "register", "new", "SLD", "slides", "--type", "slide"]) == 0
    _suggest(std, "deck")
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "throughline-deck-standard"]) == 0
    text = (tmp_path / "fol" / "throughline.toml").read_text(encoding="utf-8")
    assert 'namespace = "deck"' in text
    assert not _errors(tmp_path / "fol")


def test_named_namespace_beats_the_suggestion(tmp_path, monkeypatch):
    std = _standard(tmp_path)
    _suggest(std, "deck")
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "std", "--namespace", "slides"]) == 0
    text = (tmp_path / "fol" / "throughline.toml").read_text(encoding="utf-8")
    assert 'namespace = "slides"' in text


def test_the_suggestion_is_not_inherited_by_the_follower(tmp_path, monkeypatch):
    std = _standard(tmp_path)
    _suggest(std, "deck")
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "std"]) == 0
    assert load_project(tmp_path / "fol").schema.suggested_namespace is None


@pytest.mark.parametrize("bad", ["Deck", "9x", "a b", ""])
def test_a_suggestion_that_is_not_a_namespace_is_refused_when_read(tmp_path, bad):
    std = _standard(tmp_path)
    _suggest(std, bad)
    with pytest.raises(ProjectError, match="suggested_namespace"):
        load_project(std)


def test_a_provisional_name_that_is_not_a_namespace_still_takes_the_suggestion(
        tmp_path, monkeypatch):
    std = _standard(tmp_path, "9deck")
    _suggest(std, "deck")
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "9deck"]) == 0
    text = (tmp_path / "fol" / "throughline.toml").read_text(encoding="utf-8")
    assert 'namespace = "deck"' in text


def test_the_namespace_may_be_named(tmp_path, monkeypatch):
    _standard(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "std", "--namespace", "deck"]) == 0
    text = (tmp_path / "fol" / "throughline.toml").read_text(encoding="utf-8")
    assert 'namespace = "deck"' in text


def test_a_namespace_its_items_cannot_be_borrowed_under_is_refused(tmp_path, monkeypatch, capsys):
    std = _standard(tmp_path)
    assert _cli(["-C", std, "register", "new", "SLD", "slides", "--type", "slide"]) == 0
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "std",
                 "--namespace", "averyverylongname"]) == 2
    assert "--namespace" in capsys.readouterr().err
    assert not (tmp_path / "fol").exists()


def test_an_invalid_namespace_is_refused_before_anything_is_written(tmp_path, monkeypatch):
    _standard(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "std", "--namespace", "9x"]) == 2
    assert not (tmp_path / "fol").exists()


# --------------------------------------------------------------------------- #
# SR-0296 — init creates the registers a standard declares by items; one that
# declares only in its configuration gives none (tests/test_declared.py has the rest)
# --------------------------------------------------------------------------- #

def test_a_standard_declaring_no_registers_gives_none_and_says_so(tmp_path, monkeypatch, capsys):
    _standard(tmp_path, extra='\n[types.slide]\n')
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "std"]) == 0
    assert "declares no registers" in capsys.readouterr().out
    assert not load_project(tmp_path / "fol").registers
