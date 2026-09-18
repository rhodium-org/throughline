# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Transitive sources are bound by composing (UR-0005, SR-0045, SR-0015).

Composing a source composes what it composes. Every namespace a source declares
— and every one those declare, to any depth — is bound into the consumer's union
under the label the declaring source gave it, at the edition it pinned. Nothing is
opted into; the closure is what "compose this source" means. The consumer's one
decision is the label, made with ``alias`` on a declared source, and a label that
reaches the union at two editions is the collision SR-0015 governs.

Written before the change: every test here except the two marked ``xfail``-free
sanity checks is expected to FAIL on tl-compose 0.16.4 and pass afterwards.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from throughline.cli import main as tlc_main


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("TL_CACHE", str(tmp_path / "cache"))


# ---------------------------------------------------------------------- fixtures

_CONSUMER_TAIL = (
    '[grounding]\nroot_types = ["intent"]\ndelivery_roots = ["intent"]\n'
    'ground_link_types = ["derives_from"]\n\n'
    '[links]\ntypes = ["derives_from", "relates"]\n\n'
    '[status]\nvalues = ["draft", "approved", "ratified", "rejected", "suspect", '
    '"deleted"]\n\n'
    '[status.roles]\ninitial = "draft"\nratified = "ratified"\ninvalidated = "rejected"\n'
    'suspect = "suspect"\ntombstone = "deleted"\n')


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(body, encoding="utf-8")
    return root


def _project(name: str, *, cites: list[str] = (), sources: str = "",
             clause: str | None = None, consumer: bool = False) -> dict[str, str]:
    """A minimal throughline project with one root intent and one SR that may cite
    other namespaces. ``sources`` is appended verbatim to throughline.toml."""
    fmt = 3 if consumer else 2
    toml = f'[project]\nname = "{name}"\nformat_version = {fmt}\n{sources}'
    if consumer:
        toml += "\n" + _CONSUMER_TAIL
    links = "".join(f"- target: {t}\n  type: relates\n" for t in cites)
    return {
        "throughline.toml": toml,
        "intents/.register.yml": "prefix: INT\ndigits: 4\n",
        "system-requirements/.register.yml": "prefix: SR\ndigits: 4\n",
        "intents/INT-0001.yml": (
            f"uid: INT-0001\ntype: intent\nstatus: approved\ntitle: {name} root\n"
            "text: The root.\nnormative: false\n"),
        "system-requirements/SR-0001.yml": (
            f"uid: SR-0001\ntype: system_requirement\nstatus: approved\n"
            f"title: {name} clause\ntext: {clause or name + ' clause.'}\n"
            f"links:\n- target: INT-0001\n  type: derives_from\n{links}"
            "attrs:\n  priority: must\n  origin: human\n"),
    }


def _src(ns: str, path: str, extra: str = "") -> str:
    return f'\n[[sources]]\nnamespace = "{ns}"\npath = "{path}"\n{extra}'


def _check(consumer: Path, capsys) -> tuple[int, str]:
    rc = tlc_main(["-C", str(consumer), "check", "--strict", "--base", ""])
    cap = capsys.readouterr()
    return rc, cap.out + cap.err


def _query(consumer: Path, capsys, expr: str) -> str:
    tlc_main(["-C", str(consumer), "query", expr])
    cap = capsys.readouterr()
    return cap.out + cap.err


# --------------------------------------------------- depth: the closure is bound

def _chain(tmp_path: Path) -> Path:
    """regulation <- platform <- house <- consumer, each citing the one below.
    The consumer declares only ``house``."""
    _write(tmp_path / "regulation", _project("regulation"))
    _write(tmp_path / "platform", _project(
        "platform", cites=["regulation:SR-0001"],
        sources=_src("regulation", "../regulation")))
    _write(tmp_path / "house", _project(
        "house", cites=["platform:SR-0001"],
        sources=_src("platform", "../platform")))
    return _write(tmp_path / "consumer", _project(
        "consumer", cites=["house:SR-0001"], consumer=True,
        sources=_src("house", "../house")))


def test_three_level_chain_composes_from_one_declaration(tmp_path, capsys):
    consumer = _chain(tmp_path)
    rc, out = _check(consumer, capsys)
    assert rc == 0, out
    assert "composed graph is sound" in out
    # Every namespace in the closure is bound and listed, with its provenance.
    assert "3 source(s) composed" in out
    assert "regulation" in out and "platform" in out and "house" in out
    assert "via house" in out  # a transitive source says what carried it


def test_consumer_may_cite_a_transitive_namespace_by_its_hoisted_label(tmp_path, capsys):
    # Traceability is the point: the consumer can link straight to the clause of
    # the regulation that house reaches only through platform.
    consumer = _chain(tmp_path)
    files = _project("consumer", cites=["house:SR-0001", "regulation:SR-0001"],
                     consumer=True, sources=_src("house", "../house"))
    _write(consumer, files)
    rc, out = _check(consumer, capsys)
    assert rc == 0, out
    listing = _query(consumer, capsys, "type == 'system_requirement'")
    assert "regulation:SR-0001" in listing


def test_undeclared_namespace_error_names_where_the_reference_lives(tmp_path, capsys):
    # After the change the only reference that can name an unbound namespace is
    # one the consumer wrote itself — and the error should say so, and name the
    # namespaces that *are* reachable so the fix is a one-line edit.
    consumer = _chain(tmp_path)
    files = _project("consumer", cites=["wcag:SR-0001"], consumer=True,
                     sources=_src("house", "../house"))
    _write(consumer, files)
    rc, out = _check(consumer, capsys)
    assert rc != 0
    assert "wcag" in out
    assert "SR-0001" in out                 # the consumer item carrying it
    assert "house" in out and "regulation" in out  # what it could have cited


# ------------------------------------------ labels are never captured (was Break 2)

def _capture_scene(tmp_path: Path, consumer_platform: str) -> Path:
    """house-b calls its dependency ``platform`` and pins it to platform-old. The
    consumer also declares a source called ``platform``, pointing wherever
    ``consumer_platform`` says."""
    _write(tmp_path / "platform-old", _project("platform-old",
                                               clause="OLD edition clause."))
    _write(tmp_path / "something-else", _project("something-else"))
    _write(tmp_path / "house-b", _project(
        "house-b", cites=["platform:SR-0001"],
        sources=_src("platform", "../platform-old")))
    return _write(tmp_path / "consumer", _project(
        "consumer", cites=["house:SR-0001"], consumer=True,
        sources=_src("house", "../house-b") + _src("platform", consumer_platform)))


def test_a_sources_internal_label_is_never_captured_by_the_consumers(tmp_path, capsys):
    # On 0.16.4 this composes clean and house-b's link silently points at
    # something-else. It must instead be the SR-0015 collision: one label,
    # two editions, both origins named.
    consumer = _capture_scene(tmp_path, "../something-else")
    rc, out = _check(consumer, capsys)
    assert rc != 0
    assert "two different editions" in out
    assert "platform-old" in out and "something-else" in out
    assert "via house" in out or "from 'house'" in out  # says which path carried it
    assert "alias" in out                                # the fix is named


def test_same_edition_under_the_same_label_binds_once(tmp_path, capsys):
    # The consumer happens to declare the very edition house-b pinned. That is
    # one source, bound once, and the consumer's own declaration wins the name.
    consumer = _capture_scene(tmp_path, "../platform-old")
    rc, out = _check(consumer, capsys)
    assert rc == 0, out
    assert "2 source(s) composed" in out


# ------------------------------------------ same edition, two labels: coalesce

def test_same_edition_under_two_labels_coalesces_and_says_so(tmp_path, capsys):
    _write(tmp_path / "asvs", _project("asvs"))
    _write(tmp_path / "house", _project(
        "house", cites=["asvs:SR-0001"], sources=_src("asvs", "../asvs")))
    _write(tmp_path / "platform", _project(
        "platform", cites=["owasp:SR-0001"], sources=_src("owasp", "../asvs")))
    consumer = _write(tmp_path / "consumer", _project(
        "consumer", cites=["house:SR-0001", "platform:SR-0001"], consumer=True,
        sources=_src("house", "../house") + _src("platform", "../platform")))
    rc, out = _check(consumer, capsys)
    assert rc == 0, out
    # Bound once: house, platform, and one asvs — not four sources.
    assert "3 source(s) composed" in out
    # The coalescence is reported, naming both labels, so a reader of the
    # summary knows why `owasp:` never appears in the union.
    assert "owasp" in out and "asvs" in out
    listing = _query(consumer, capsys, "type == 'system_requirement'")
    assert listing.count("SR-0001") == 4  # consumer, house, platform, asvs
    assert "owasp:" not in listing


# --------------------------------------- two editions, one label: alias apart

def _diamond(tmp_path: Path, house_alias: str = "") -> Path:
    _write(tmp_path / "asvs-v4", _project("asvs", clause="v4 clause."))
    _write(tmp_path / "asvs-v5", _project("asvs", clause="v5 clause."))
    _write(tmp_path / "house", _project(
        "house", cites=["asvs:SR-0001"], sources=_src("asvs", "../asvs-v4")))
    _write(tmp_path / "platform", _project(
        "platform", cites=["asvs:SR-0001"], sources=_src("asvs", "../asvs-v5")))
    return _write(tmp_path / "consumer", _project(
        "consumer", cites=["house:SR-0001", "platform:SR-0001"], consumer=True,
        sources=_src("house", "../house", house_alias)
        + _src("platform", "../platform")))


def test_two_editions_under_one_label_fail_with_why_and_fix(tmp_path, capsys):
    consumer = _diamond(tmp_path)
    rc, out = _check(consumer, capsys)
    assert rc != 0
    assert "two different editions" in out
    assert "asvs-v4" in out and "asvs-v5" in out
    assert "via house" in out and "via platform" in out
    assert "alias" in out
    assert "merge" in out                 # states it will not merge


def test_alias_on_the_carrying_source_splits_the_diamond(tmp_path, capsys):
    consumer = _diamond(tmp_path, house_alias='alias = { asvs = "asvs-v4" }\n')
    rc, out = _check(consumer, capsys)
    assert rc == 0, out
    assert "4 source(s) composed" in out
    listing = _query(consumer, capsys, "type == 'system_requirement'")
    assert "asvs-v4:SR-0001" in listing and "asvs:SR-0001" in listing
    # house's own reference followed the alias; platform's did not. `subgraph`
    # walks the item named even when it is borrowed (SR-0041); `trace` stops at
    # the source boundary and would show no outgoing link at all (SR-0020).
    tlc_main(["-C", str(consumer), "subgraph", "house:SR-0001"])
    neighbourhood = capsys.readouterr().out
    assert "asvs-v4:SR-0001" in neighbourhood
    tlc_main(["-C", str(consumer), "subgraph", "platform:SR-0001"])
    neighbourhood = capsys.readouterr().out
    assert "asvs:SR-0001" in neighbourhood and "asvs-v4:" not in neighbourhood


def test_alias_applies_throughout_the_carrying_sources_subtree(tmp_path, capsys):
    # The diamond is one level deeper: house reaches asvs through platform-a,
    # the consumer reaches a different asvs through platform-b. The consumer has
    # no entry for platform-a to hang an alias on, so the alias on house must
    # reach down.
    _write(tmp_path / "asvs-v4", _project("asvs", clause="v4 clause."))
    _write(tmp_path / "asvs-v5", _project("asvs", clause="v5 clause."))
    _write(tmp_path / "platform-a", _project(
        "platform-a", cites=["asvs:SR-0001"], sources=_src("asvs", "../asvs-v4")))
    _write(tmp_path / "house", _project(
        "house", cites=["pa:SR-0001"], sources=_src("pa", "../platform-a")))
    _write(tmp_path / "platform-b", _project(
        "platform-b", cites=["asvs:SR-0001"], sources=_src("asvs", "../asvs-v5")))
    consumer = _write(tmp_path / "consumer", _project(
        "consumer", cites=["house:SR-0001", "pb:SR-0001"], consumer=True,
        sources=_src("house", "../house", 'alias = { asvs = "asvs-v4" }\n')
        + _src("pb", "../platform-b")))
    rc, out = _check(consumer, capsys)
    assert rc == 0, out
    assert "5 source(s) composed" in out


# ------------------------------------------------------------ the old keyword

def test_reexport_keyword_is_refused_and_points_at_alias(tmp_path, capsys):
    consumer = _chain(tmp_path)
    files = _project("consumer", cites=["house:SR-0001"], consumer=True,
                     sources=_src("house", "../house", 'reexport = ["platform"]\n'))
    _write(consumer, files)
    rc, out = _check(consumer, capsys)
    assert rc != 0
    assert "reexport" in out
    assert "alias" in out
    assert "SR-0045" in out


# ----------------------------------------------------------------- termination

def test_a_dependency_cycle_terminates_and_is_named(tmp_path, capsys):
    _write(tmp_path / "a", _project("a", cites=["b:SR-0001"],
                                    sources=_src("b", "../b")))
    _write(tmp_path / "b", _project("b", cites=["a:SR-0001"],
                                    sources=_src("a", "../a")))
    consumer = _write(tmp_path / "consumer", _project(
        "consumer", cites=["a:SR-0001"], consumer=True,
        sources=_src("a", "../a")))
    rc, out = _check(consumer, capsys)
    # Either verdict is acceptable; what is not acceptable is a RecursionError
    # or a hang. If it composes, `a` binds once (the consumer's declaration) and
    # `b` once; if it refuses, it says "cycle" and names the path.
    if rc != 0:
        assert "cycle" in out.lower() and "a" in out and "b" in out
    else:
        assert "2 source(s) composed" in out


def test_shared_dependency_resolves_once(tmp_path, capsys):
    # Two declared sources both pin the same regulation. It is fetched/loaded
    # once and bound once; SR-0044's shared-cache rule already covers url
    # sources, this pins the union-level behaviour.
    _write(tmp_path / "regulation", _project("regulation"))
    _write(tmp_path / "house", _project(
        "house", cites=["reg:SR-0001"], sources=_src("reg", "../regulation")))
    _write(tmp_path / "platform", _project(
        "platform", cites=["reg:SR-0001"], sources=_src("reg", "../regulation")))
    consumer = _write(tmp_path / "consumer", _project(
        "consumer", cites=["house:SR-0001", "platform:SR-0001"], consumer=True,
        sources=_src("house", "../house") + _src("platform", "../platform")))
    rc, out = _check(consumer, capsys)
    assert rc == 0, out
    assert "3 source(s) composed" in out


# ------------------------------- a fetched source whose dependency is a path

def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=str(cwd), check=True, capture_output=True, text=True)


def test_fetched_source_with_a_path_dependency_explains_itself(tmp_path, capsys):
    # A source published by url cannot carry a `path` dependency: relative to the
    # cache it points nowhere. This will now happen more often, so the failure
    # must say what is wrong and what a publishable source has to do instead.
    origin = tmp_path / "origin"
    _write(origin, _project("house", cites=["asvs:SR-0001"],
                            sources=_src("asvs", "../asvs")))
    _git("init", "-b", "main", cwd=origin)
    _git("add", ".", cwd=origin)
    _git("commit", "-m", "edition", cwd=origin)
    _git("tag", "v1", cwd=origin)
    consumer = _write(tmp_path / "consumer", _project(
        "consumer", cites=["house:SR-0001"], consumer=True,
        sources=(f'\n[[sources]]\nnamespace = "house"\n'
                 f'url = "{origin.as_uri()}"\nref = "v1"\n')))
    rc, out = _check(consumer, capsys)
    assert rc != 0
    assert "asvs" in out and "path" in out
    assert "via house" in out or "house" in out
    assert "url" in out and "ref" in out  # the remedy: pin it by url + ref upstream


# ----------------------------------- a source's view of its own transitive labels

def test_a_source_may_cite_its_own_transitive_namespace_by_its_hoisted_label(
        tmp_path, capsys):
    # house was itself a consumer when authored, so its items may cite regulation
    # directly — the label platform hoisted into house's union. Composing house
    # must carry that view with it, or house's own reference cannot resolve.
    _write(tmp_path / "regulation", _project("regulation"))
    _write(tmp_path / "platform", _project(
        "platform", cites=["regulation:SR-0001"],
        sources=_src("regulation", "../regulation")))
    _write(tmp_path / "house", _project(
        "house", cites=["platform:SR-0001", "regulation:SR-0001"],
        sources=_src("platform", "../platform")))
    consumer = _write(tmp_path / "consumer", _project(
        "consumer", cites=["house:SR-0001"], consumer=True,
        sources=_src("house", "../house")))
    rc, out = _check(consumer, capsys)
    assert rc == 0, out
    assert "3 source(s) composed" in out


def test_an_intermediates_own_alias_is_honoured_beneath_it(tmp_path, capsys):
    # house composed platform with `alias = { asvs = "owasp" }` and cites
    # owasp:SR-0001. Composing house composes what house composes: the union binds
    # `owasp`, carried via house › platform, and house's reference resolves to it.
    _write(tmp_path / "asvs", _project("asvs"))
    _write(tmp_path / "platform", _project(
        "platform", cites=["asvs:SR-0001"], sources=_src("asvs", "../asvs")))
    _write(tmp_path / "house", _project(
        "house", cites=["platform:SR-0001", "owasp:SR-0001"],
        sources=_src("platform", "../platform", 'alias = { asvs = "owasp" }\n')))
    consumer = _write(tmp_path / "consumer", _project(
        "consumer", cites=["house:SR-0001", "owasp:SR-0001"], consumer=True,
        sources=_src("house", "../house")))
    rc, out = _check(consumer, capsys)
    assert rc == 0, out
    assert "3 source(s) composed" in out
    assert "owasp" in out and "via house › platform" in out
    listing = _query(consumer, capsys, "type == 'system_requirement'")
    assert "owasp:SR-0001" in listing and "asvs:" not in listing
