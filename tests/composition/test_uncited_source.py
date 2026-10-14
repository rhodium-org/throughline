# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A declared source that nothing cites is reported (SR-0253).

A source no local item links to has no effect on the graph, so a speculative or
mistaken declaration used to be pronounced sound. These tests hold the rule's four
edges: it fires on the source the consumer declared, a link through a transitive
source counts for the source that carried it, ``uncited = "<reason>"`` exempts a
deliberate declaration and goes stale loudly once something cites it, and
``[rules]`` sets its severity like any other rule.
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from throughline.cli import main as tl_main
from throughline.sources import SourceError, parse_sources


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("TL_CACHE", str(tmp_path / "cache"))


_TAIL = (
    '[grounding]\nroot_types = ["intent"]\ndelivery_roots = ["intent"]\n'
    'ground_link_types = ["derives_from"]\n\n'
    '[links]\ntypes = ["derives_from", "relates"]\n\n'
    '[status]\nvalues = ["draft", "approved", "ratified", "rejected", "suspect", '
    '"deleted"]\n\n'
    '[status.roles]\ninitial = "draft"\nratified = "ratified"\ninvalidated = "rejected"\n'
    'suspect = "suspect"\ntombstone = "deleted"\n')


def _write(root: Path, name: str, *, cites=(), sources: str = "", extra: str = "") -> Path:
    links = "".join(f"- target: {t}\n  type: relates\n" for t in cites)
    files = {
        "throughline.toml": (f'[project]\nname = "{name}"\nformat_version = 3\n'
                             f"{sources}\n{_TAIL}{extra}"),
        "intents/.register.yml": "prefix: INT\ndigits: 4\n",
        "system-requirements/.register.yml": "prefix: SR\ndigits: 4\n",
        "intents/INT-0001.yml": (
            f"uid: INT-0001\ntype: intent\nstatus: approved\ntitle: {name} root\n"
            "text: The root.\nnormative: false\n"),
        "system-requirements/SR-0001.yml": (
            f"uid: SR-0001\ntype: system_requirement\nstatus: approved\n"
            f"title: {name} clause\ntext: The {name} clause.\n"
            f"links:\n- target: INT-0001\n  type: derives_from\n{links}"
            "attrs:\n  priority: must\n  origin: human\n"),
    }
    for rel, body in files.items():
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(body, encoding="utf-8")
    return root


def _src(ns: str, path: str, extra: str = "") -> str:
    return f'\n[[sources]]\nnamespace = "{ns}"\npath = "{path}"\n{extra}'


def _check(consumer: Path, capsys, *, strict: bool = False):
    argv = ["-C", str(consumer), "check", "--base", "", "--format", "json"]
    if strict:
        argv.append("--strict")
    rc = tl_main(argv)
    findings = json.loads(capsys.readouterr().out)
    return rc, [f for f in findings if f["rule"] == "uncited-source"]


@pytest.fixture
def scene(tmp_path):
    """Two sources beside a consumer that will declare them as each test needs."""
    _write(tmp_path / "asvs", "asvs")
    _write(tmp_path / "eugdpr", "eugdpr")
    return tmp_path


def test_a_cited_source_is_not_reported(scene, capsys):
    consumer = _write(scene / "consumer", "consumer", cites=["asvs:SR-0001"],
                      sources=_src("asvs", "../asvs"))
    rc, found = _check(consumer, capsys, strict=True)
    assert rc == 0
    assert found == []


def test_an_uncited_source_is_a_warning_that_strict_raises(scene, capsys):
    consumer = _write(scene / "consumer", "consumer", cites=["asvs:SR-0001"],
                      sources=_src("asvs", "../asvs") + _src("eugdpr", "../eugdpr"))
    rc, found = _check(consumer, capsys)
    assert rc == 0, "a plain check must not fail on an upgrade"
    assert [f["severity"] for f in found] == ["warning"]
    assert "'eugdpr'" in found[0]["message"]
    assert 'uncited = "<reason>"' in found[0]["message"]

    rc, found = _check(consumer, capsys, strict=True)
    assert rc == 1
    assert [f["severity"] for f in found] == ["error"]


def test_a_deleted_items_link_does_not_count_as_a_citation(scene, capsys):
    consumer = _write(scene / "consumer", "consumer", sources=_src("asvs", "../asvs"))
    (consumer / "system-requirements" / "SR-0002.yml").write_text(
        "uid: SR-0002\ntype: system_requirement\nstatus: deleted\ntitle: gone\n"
        "text: Gone.\nlinks:\n- target: INT-0001\n  type: derives_from\n"
        "- target: asvs:SR-0001\n  type: relates\n"
        "attrs:\n  priority: must\n  origin: human\n", encoding="utf-8")
    _, found = _check(consumer, capsys)
    assert len(found) == 1 and "'asvs'" in found[0]["message"]


def test_a_link_through_a_transitive_source_cites_the_source_that_carried_it(
        scene, capsys):
    # `practical` carries eugdpr in; citing eugdpr is the reason for declaring it.
    _write(scene / "practical", "practical", cites=["eugdpr:SR-0001"],
           sources=_src("eugdpr", "../eugdpr"))
    consumer = _write(scene / "consumer", "consumer", cites=["eugdpr:SR-0001"],
                      sources=_src("practical", "../practical"))
    rc, found = _check(consumer, capsys, strict=True)
    assert rc == 0, found
    assert found == []


def test_an_uncited_reason_exempts_the_source(scene, capsys):
    consumer = _write(
        scene / "consumer", "consumer",
        sources=_src("asvs", "../asvs",
                     'uncited = "composed so tl context shows the whole picture"\n'))
    rc, found = _check(consumer, capsys, strict=True)
    assert rc == 0
    assert found == []


def test_an_uncited_reason_on_a_cited_source_is_reported_as_stale(scene, capsys):
    consumer = _write(scene / "consumer", "consumer", cites=["asvs:SR-0001"],
                      sources=_src("asvs", "../asvs", 'uncited = "not needed yet"\n'))
    _, found = _check(consumer, capsys)
    assert len(found) == 1
    assert "remove the 'uncited' key" in found[0]["message"]
    assert "not needed yet" in found[0]["message"]


@pytest.mark.parametrize("value", ['""', '"   "', "true", "1"])
def test_an_uncited_value_that_is_not_a_reason_is_refused(value):
    # `uncited = true` would exempt the source while saying nothing: the
    # comment-beside-the-config the key exists to replace.
    class _Cfg:
        config = {"sources": [{"namespace": "asvs", "path": "../asvs",
                               "uncited": tomllib.loads(f"v = {value}")["v"]}]}
    with pytest.raises(SourceError) as exc:
        parse_sources(_Cfg())
    assert "'asvs'" in str(exc.value) and "reason" in str(exc.value)


def test_rules_table_sets_the_severity(scene, capsys):
    off = _write(scene / "off", "off", sources=_src("asvs", "../asvs"),
                 extra='\n[rules]\nuncited-source = "off"\n')
    rc, found = _check(off, capsys, strict=True)
    assert rc == 0 and found == []

    err = _write(scene / "err", "err", sources=_src("asvs", "../asvs"),
                 extra='\n[rules]\nuncited-source = "error"\n')
    rc, found = _check(err, capsys)
    assert rc == 1
    assert [f["severity"] for f in found] == ["error"]
