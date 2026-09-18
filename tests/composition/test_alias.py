# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""The alias table, the label map, and the withdrawn ``reexport`` key (UR-0005,
SR-0045, SR-0015).

Composing a source composes what it composes. The consumer's one lever over a
transitive label is ``alias`` on the declared source carrying it; a source's own
references resolve through its own label map and never against the union's
namespace set; and the ``reexport`` key SR-0014 introduced is refused in the
consumer's configuration and ignored, with a note, inside a source's.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from throughline.model import Item, Link, Project, Register

from throughline.cli import main as tlc_main
from throughline.sources import (
    SourceError,
    parse_sources,
    withdrawn_declarations,
)
from throughline.union import ComposeError, build_union


# ------------------------------------------------------------------ alias parsing

def _project(config: dict) -> Project:
    return Project(path=".", config=config)


def test_alias_table_binds_labels():
    p = _project({"sources": [
        {"namespace": "base", "path": "../base", "alias": {"asvs": "owasp"}}]})
    (s,) = parse_sources(p)
    assert s.alias == {"asvs": "owasp"}


def test_no_alias_is_empty():
    p = _project({"sources": [{"namespace": "base", "path": "../base"}]})
    (s,) = parse_sources(p)
    assert s.alias == {}


def test_alias_bad_label_rejected():
    p = _project({"sources": [
        {"namespace": "base", "path": "../base", "alias": {"Asvs": "owasp"}}]})
    with pytest.raises(SourceError, match="invalid label"):
        parse_sources(p)


def test_alias_bad_name_rejected():
    p = _project({"sources": [
        {"namespace": "base", "path": "../base", "alias": {"asvs": "Owasp"}}]})
    with pytest.raises(SourceError, match="invalid namespace"):
        parse_sources(p)


def test_alias_array_form_rejected():
    # There is no identity form: a label kept as its declaring source gave it needs
    # no alias at all.
    p = _project({"sources": [
        {"namespace": "base", "path": "../base", "alias": ["asvs"]}]})
    with pytest.raises(SourceError, match="table"):
        parse_sources(p)


def test_two_labels_aliased_to_one_name_rejected():
    p = _project({"sources": [
        {"namespace": "base", "path": "../base",
         "alias": {"asvs": "sec", "owasp": "sec"}}]})
    with pytest.raises(SourceError, match="one alias binds one label"):
        parse_sources(p)


# ------------------------------------------------------------ the withdrawn key

def test_reexport_is_refused_in_the_consumers_config():
    p = _project({"sources": [
        {"namespace": "base", "path": "../base", "reexport": ["asvs"]}]})
    with pytest.raises(SourceError) as e:
        parse_sources(p)
    msg = str(e.value)
    assert "reexport" in msg and "alias" in msg and "SR-0045" in msg


def test_reexport_is_ignored_in_a_sources_config():
    # A published edition may carry the key; the walk reads it as a fact and names
    # it, rather than failing a consumer who cannot edit the source.
    p = _project({"sources": [
        {"namespace": "base", "path": "../base", "reexport": {"asvs": "owasp"}},
        {"namespace": "gds", "path": "../gds"}]})
    sources = parse_sources(p, withdrawn="ignore")
    assert [s.namespace for s in sources] == ["base", "gds"]
    assert sources[0].alias == {}  # the withdrawn key is not read as an alias
    assert withdrawn_declarations(p) == ["base"]


# --------------------------------------------------------- union label map (SR-0045)

def _mk(config: dict, regs: dict[str, list[Item]]) -> Project:
    p = Project(path=".", config=config)
    for prefix, items in regs.items():
        reg = Register(prefix=prefix)
        for it in items:
            it._register_prefix = prefix
            reg.items[it.uid] = it
        p.registers[prefix] = reg
    return p


_SCHEMA = {"project": {"name": "c", "format_version": 2},
           "grounding": {"root_types": ["intent"], "delivery_roots": ["intent"],
                         "ground_link_types": ["derives_from"]}}


def _asvs() -> Project:
    return _mk({"project": {"name": "asvs", "format_version": 2}},
               {"SR": [Item(uid="SR-0001", type="system_requirement",
                            status="approved")]})


def _base_referencing_asvs() -> Project:
    # A source that internally cites another namespace by that namespace's own name.
    sr = Item(uid="SR-0001", type="system_requirement", status="approved",
              links=[Link(target="asvs:SR-0001", type="relates")])
    return _mk({"project": {"name": "base", "format_version": 2}}, {"SR": [sr]})


def test_a_sources_reference_resolves_through_its_own_label_map():
    consumer = _mk(_SCHEMA, {})
    sources = {"base": _base_referencing_asvs(), "owasp": _asvs()}
    union = build_union(consumer, sources, {"base": {"asvs": "owasp"}})
    base_sr = next(it for it in union.project.items()
                   if union.qualified(it.uid) == "base:SR-0001")
    (link,) = base_sr.links
    assert union.qualified(link.target) == "owasp:SR-0001"


def test_a_sources_reference_never_falls_through_to_the_union():
    # The union binds an `asvs`, but base's own map does not name one — so base's
    # reference must fail, not be captured by whatever the consumer called `asvs`.
    consumer = _mk(_SCHEMA, {})
    sources = {"base": _base_referencing_asvs(), "asvs": _asvs()}
    with pytest.raises(ComposeError) as e:
        build_union(consumer, sources, {"base": {}})
    msg = str(e.value)
    assert "base" in msg and "asvs:SR-0001" in msg and "does not declare" in msg


def test_a_consumer_reference_to_an_unbound_namespace_names_item_and_bound():
    consumer = _mk(_SCHEMA, {"SR": [Item(
        uid="SR-0001", type="system_requirement", status="approved",
        links=[Link(target="ghost:SR-0001", type="relates")])]})
    with pytest.raises(ComposeError) as e:
        build_union(consumer, {"asvs": _asvs()}, {"asvs": {}})
    msg = str(e.value)
    assert "SR-0001" in msg and "ghost" in msg and "asvs" in msg


# ------------------------------------------------------- end-to-end (materialised)

def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(body, encoding="utf-8")
    return root


def _leaf_source(name: str, clause_text: str) -> dict[str, str]:
    return {
        "throughline.toml": f'[project]\nname = "{name}"\nformat_version = 2\n',
        "intents/.register.yml": "prefix: INT\ndigits: 4\n",
        "system-requirements/.register.yml": "prefix: SR\ndigits: 4\n",
        "intents/INT-0001.yml": (
            "uid: INT-0001\ntype: intent\nstatus: approved\n"
            f"title: {name} root\ntext: The {name} source root.\nnormative: false\n"),
        "system-requirements/SR-0001.yml": (
            "uid: SR-0001\ntype: system_requirement\nstatus: approved\n"
            f"title: {name} clause\ntext: {clause_text}\n"
            "links:\n- target: INT-0001\n  type: derives_from\n"
            "attrs:\n  priority: must\n  origin: human\n"),
    }


def _base(asvs_entry_extra: str = "") -> dict[str, str]:
    return {
        "throughline.toml": (
            '[project]\nname = "base"\nformat_version = 2\n\n'
            '[[sources]]\nnamespace = "asvs"\npath = "../asvs-source"\n'
            f"{asvs_entry_extra}"),
        "intents/.register.yml": "prefix: INT\ndigits: 4\n",
        "system-requirements/.register.yml": "prefix: SR\ndigits: 4\n",
        "intents/INT-0001.yml": (
            "uid: INT-0001\ntype: intent\nstatus: approved\n"
            "title: base root\ntext: The base source root.\nnormative: false\n"),
        "system-requirements/SR-0001.yml": (
            "uid: SR-0001\ntype: system_requirement\nstatus: approved\n"
            "title: base clause leaning on asvs\ntext: The base clause.\n"
            "links:\n- target: INT-0001\n  type: derives_from\n"
            "- target: asvs:SR-0001\n  type: relates\n"
            "attrs:\n  priority: must\n  origin: human\n"),
    }


def _consumer(extra_line: str, ref_target: str) -> dict[str, str]:
    return {
        "throughline.toml": (
            '[project]\nname = "consumer"\nformat_version = 3\n\n'
            '[[sources]]\nnamespace = "base"\npath = "../base"\n'
            f"{extra_line}\n\n"
            '[grounding]\nroot_types = ["intent"]\n'
            'delivery_roots = ["intent"]\nground_link_types = ["derives_from"]\n\n'
            '[links]\ntypes = ["derives_from", "relates"]\n\n'
            '[status]\nvalues = ["draft", "approved", "ratified", "rejected", '
            '"suspect", "deleted"]\n\n'
            '[status.roles]\ninitial = "draft"\nratified = "ratified"\n'
            'invalidated = "rejected"\nsuspect = "suspect"\ntombstone = "deleted"\n'),
        "intents/.register.yml": "prefix: INT\ndigits: 4\n",
        "system-requirements/.register.yml": "prefix: SR\ndigits: 4\n",
        "intents/INT-0001.yml": (
            "uid: INT-0001\ntype: intent\nstatus: approved\n"
            "title: consumer root\ntext: The consumer root.\nnormative: false\n"),
        "system-requirements/SR-0001.yml": (
            "uid: SR-0001\ntype: system_requirement\nstatus: approved\n"
            "title: consumer clause\ntext: The consumer clause.\n"
            "links:\n- target: INT-0001\n  type: derives_from\n"
            f"- target: {ref_target}\n  type: relates\n"
            "attrs:\n  priority: must\n  origin: human\n"),
    }


def _scene(tmp_path: Path, extra_line: str, ref_target: str,
           base_extra: str = "") -> Path:
    _write(tmp_path / "asvs-source", _leaf_source("asvs", "The asvs clause."))
    _write(tmp_path / "base", _base(base_extra))
    return _write(tmp_path / "consumer", _consumer(extra_line, ref_target))


def _check(consumer: Path, capsys) -> tuple[int, str]:
    rc = tlc_main(["-C", str(consumer), "check", "--base", ""])
    cap = capsys.readouterr()
    return rc, cap.out + cap.err


def test_transitive_source_is_bound_without_being_named(tmp_path, capsys):
    consumer = _scene(tmp_path, "", "asvs:SR-0001")
    rc, out = _check(consumer, capsys)
    assert rc == 0, out
    assert "composed graph is sound" in out
    assert "2 source(s) composed" in out
    assert "via base" in out  # the summary says what carried asvs in


def test_alias_binds_the_transitive_label_to_a_new_name(tmp_path, capsys):
    consumer = _scene(tmp_path, 'alias = { asvs = "owasp" }', "owasp:SR-0001")
    rc, out = _check(consumer, capsys)
    assert rc == 0, out
    assert "owasp" in out and "via base" in out


def test_two_editions_conflict_advises_why_and_fix(tmp_path, capsys):
    # The consumer declares `asvs` directly at one edition; base carries its own
    # `asvs` at a different edition — the collision SR-0015 governs.
    _write(tmp_path / "asvs-two", _leaf_source("asvs", "A DIFFERENT asvs edition."))
    files = _consumer("", "asvs:SR-0001")
    files["throughline.toml"] = files["throughline.toml"].replace(
        '[[sources]]\nnamespace = "base"\npath = "../base"\n',
        '[[sources]]\nnamespace = "base"\npath = "../base"\n\n'
        '[[sources]]\nnamespace = "asvs"\npath = "../asvs-two"\n')
    _write(tmp_path / "asvs-source", _leaf_source("asvs", "The asvs clause."))
    _write(tmp_path / "base", _base())
    consumer = _write(tmp_path / "consumer", files)

    rc, out = _check(consumer, capsys)
    assert rc != 0
    assert "two different editions" in out          # the why
    assert "declared by you" in out                 # one origin is the consumer's
    assert "via base" in out                        # the other was carried in
    assert "pinning 'asvs'" in out                  # fix 1
    assert "alias" in out                           # fix 2
    assert "merge" in out                           # states it will not merge


def test_reexport_in_the_consumers_config_is_refused_by_the_cli(tmp_path, capsys):
    consumer = _scene(tmp_path, 'reexport = ["asvs"]', "asvs:SR-0001")
    rc, out = _check(consumer, capsys)
    assert rc != 0
    assert "reexport" in out and "alias" in out and "SR-0045" in out


def test_reexport_inside_a_source_is_ignored_with_a_note(tmp_path, capsys):
    # base's own toml still carries the key on its asvs entry, as a published
    # edition might. The run composes and the summary says the key was ignored.
    consumer = _scene(tmp_path, "", "asvs:SR-0001",
                      base_extra='reexport = ["nothing"]\n')
    rc, out = _check(consumer, capsys)
    assert rc == 0, out
    assert "2 source(s) composed" in out
    assert "note:" in out and "reexport" in out and "withdrawn" in out
