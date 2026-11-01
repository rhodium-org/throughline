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

[[starter_registers]]
prefix = "SLD"
dir = "slides"
type = "slide"
title = "Slides"
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
    return [f for f in validate(load_project(root)) if f.severity == "error"]


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
    assert set(project.registers) == {"SLD"}
    assert not list(project.items())
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
# SR-0284 — a standard may declare the registers its followers start with
# --------------------------------------------------------------------------- #

def test_a_standard_without_starter_registers_gives_none_and_says_so(tmp_path, monkeypatch, capsys):
    _standard(tmp_path, extra='\n[types.slide]\n')
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "std"]) == 0
    assert "no starter registers" in capsys.readouterr().out
    assert not load_project(tmp_path / "fol").registers


def test_starter_registers_hold_the_declared_type_and_title(tmp_path, monkeypatch):
    _standard(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "std"]) == 0
    reg = load_project(tmp_path / "fol").registers["SLD"]
    assert (reg.type, reg.title, reg.path.name) == ("slide", "Slides", "slides")
    assert _cli(["-C", "fol", "new", "SLD", "--title", "cover", "--attr", "layout=cards",
                 "--no-interactive"]) == 0
    assert load_project(tmp_path / "fol").get("SLD-0001").type == "slide"


def test_the_title_defaults_to_the_folder(tmp_path, monkeypatch):
    _standard(tmp_path, extra='\n[types.slide]\n\n[[starter_registers]]\n'
                              'prefix = "SLD"\ndir = "slides"\ntype = "slide"\n')
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "std"]) == 0
    assert load_project(tmp_path / "fol").registers["SLD"].title == "slides"


def test_starter_registers_are_not_inherited_by_the_follower(tmp_path, monkeypatch):
    _standard(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "std"]) == 0
    assert load_project(tmp_path / "fol").schema.starter_registers == ()


def test_a_follower_keeps_its_registers_when_the_standard_changes_them(tmp_path, monkeypatch):
    std = _standard(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "std"]) == 0
    cfg = std / "throughline.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace('prefix = "SLD"', 'prefix = "SL"')
                   .replace('dir = "slides"', 'dir = "pages"'), encoding="utf-8")
    project = load_project(tmp_path / "fol")
    assert set(project.registers) == {"SLD"}
    assert (tmp_path / "fol" / "slides").is_dir() and not (tmp_path / "fol" / "pages").exists()


def test_a_follower_may_drop_a_starter_register(tmp_path, monkeypatch):
    _standard(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "std"]) == 0
    shutil.rmtree(tmp_path / "fol" / "slides")
    assert not load_project(tmp_path / "fol").registers


@pytest.mark.parametrize("entry, says", [
    ('prefix = "X"\ndir = "x"\ntype = "slide"', "register prefix"),
    ('prefix = "SLD"\ndir = "slides"\ntype = "video"', "does not declare"),
    ('prefix = "SLD"\ndir = "../out"\ntype = "slide"', "inside the project"),
    ('prefix = "SLD"\ndir = "/abs"\ntype = "slide"', "inside the project"),
    ('prefix = "SLD"\ndir = "slides"\ntype = "slide"\ncolour = "red"', "unknown key"),
    ('prefix = "SLD"\ndir = "slides"\ntype = "slide"\ntitle = " "', "empty"),
])
def test_a_bad_starter_register_is_refused_when_the_configuration_is_read(tmp_path, entry, says):
    std = _standard(tmp_path, extra=f"\n[types.slide]\n\n[[starter_registers]]\n{entry}\n")
    with pytest.raises(ProjectError, match=says):
        load_project(std)


@pytest.mark.parametrize("second, says", [
    ('prefix = "SLD"\ndir = "more"\ntype = "slide"', "prefix SLD is declared twice"),
    ('prefix = "MORE"\ndir = "slides"\ntype = "slide"', "folder 'slides' is declared twice"),
])
def test_starter_prefixes_and_folders_are_unique(tmp_path, second, says):
    std = _standard(tmp_path, extra=STANDARD + f"\n[[starter_registers]]\n{second}\n")
    with pytest.raises(ProjectError, match=says):
        load_project(std)


def test_a_standard_with_a_bad_starter_register_starts_no_follower(tmp_path, monkeypatch):
    _standard(tmp_path, extra='\n[types.slide]\n\n[[starter_registers]]\n'
                              'prefix = "SLD"\ndir = "slides"\ntype = "video"\n')
    monkeypatch.chdir(tmp_path)
    assert _cli(["-C", "fol", "init", "--from-schema", "std"]) == 2
    assert not (tmp_path / "fol").exists()
