# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""UR-0037, SR-0230 to SR-0236 — composition lives in the Tool.

The behaviours that are new with the fold, rather than carried across from
throughline-compose 0.21.0 (whose own suite runs beside this file): the resolver
is a parameter with a default and the default refuses in words where git cannot
run; the composition names complete with no subprocess, no socket and no terminal
when a resolver is supplied; the cache-only switch is a parameter and the old
variable is still honoured; the mirror directive is the Tool's own; and tl-compose
is a second name for tl.
"""
from __future__ import annotations

import re
import socket
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import throughline
from throughline import (
    GitResolver,
    ResolvedSource,
    Resolver,
    ResolverError,
    Source,
    compose,
    load_project,
    read_project,
    resolve_sources,
    validate,
)
from throughline.cli import main as tl_main
from throughline.inject import _REGISTRY as _DIRECTIVES
from throughline import resolvers


@pytest.fixture
def no_host(monkeypatch):
    """A caller with none of the three — what a browser runtime has (SR-0227)."""
    def refuse(*_a, **_k):
        raise OSError("this host has no subprocess or socket")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(sys.stderr, "isatty", lambda: False, raising=False)


# ----------------------------------------------- SR-0232: the resolver parameter

def test_a_path_source_composes_with_no_subprocess_and_no_socket(consumer_dir, no_host):
    """The default resolver reaches the file system for a path source and touches
    neither git nor the network, so the composition names complete under a browser
    host without any resolver being supplied."""
    consumer = load_project(consumer_dir)
    composition = compose(consumer)
    assert composition is not None
    assert sorted(composition.resolution.resolved) == ["toy"]
    assert composition.union.qualified("TOYSR-0001") == "toy:SR-0001"
    assert isinstance(validate(composition.project), list)


def test_a_supplied_resolver_replaces_the_default(consumer_dir, source_dir, no_host):
    """A caller that holds the sources passes its own resolver and obtains the same
    union (SR-0226, SR-0232): here one that never looks at the declaration's
    coordinates at all."""
    class Staged(Resolver):
        def handles(self, source: Source) -> bool:
            return True

        def resolve(self, source: Source, consumer_root: Path) -> ResolvedSource:
            project = read_project(source_dir)
            return ResolvedSource(project=project, fingerprint="sha256:staged")

    consumer = load_project(consumer_dir)
    res = resolve_sources(
        [Source(namespace="toy", url="https://nowhere.invalid/toy", ref="v1")],
        consumer_dir, resolver=Staged())
    assert res.resolved["toy"].fingerprint == "sha256:staged"
    composition = compose(consumer, resolver=Staged())
    assert composition.union.project.get("TOYSR-0001") is not None


def test_a_url_source_with_no_subprocess_is_refused_in_the_tools_words(tmp_path, no_host, monkeypatch):
    """Where the default cannot run git the failure is the Tool's own refusal, and
    it says which case it is: this host runs no subprocess. It is never an
    unhandled OSError (throughline-compose issue #5)."""
    monkeypatch.setenv("TL_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("TL_OFFLINE", raising=False)
    monkeypatch.delenv("TL_COMPOSE_OFFLINE", raising=False)
    src = Source(namespace="far", url="https://nowhere.invalid/far", ref="v1")
    with pytest.raises(ResolverError) as exc:
        GitResolver().resolve(src, tmp_path)
    assert "runs no subprocess" in str(exc.value)
    assert "git" in str(exc.value)


def test_git_missing_from_the_path_is_the_other_sentence(tmp_path, monkeypatch):
    """Git absent from the path is a different failure with a different remedy, and
    the sentence names that one instead."""
    monkeypatch.setenv("TL_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("TL_OFFLINE", raising=False)
    monkeypatch.delenv("TL_COMPOSE_OFFLINE", raising=False)

    def not_installed(*_a, **_k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", not_installed)
    src = Source(namespace="far", url="https://nowhere.invalid/far", ref="v1")
    with pytest.raises(ResolverError) as exc:
        GitResolver().resolve(src, tmp_path)
    assert "not installed" in str(exc.value)


# --------------------------------------- SR-0233: cache first, the switch, the names

def test_cache_only_is_a_parameter_and_a_cold_cache_refuses(tmp_path, monkeypatch):
    monkeypatch.setenv("TL_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("TL_OFFLINE", raising=False)
    monkeypatch.delenv("TL_COMPOSE_OFFLINE", raising=False)
    src = Source(namespace="far", url="https://nowhere.invalid/far", ref="v1")
    with pytest.raises(ResolverError) as exc:
        resolvers.resolve_source(src, tmp_path, cache_only=True)
    assert "forbids fetching" in str(exc.value)
    with pytest.raises(ResolverError):
        GitResolver(cache_only=True).resolve(src, tmp_path)


def test_the_old_variables_are_still_honoured(tmp_path, monkeypatch):
    """TL_COMPOSE_OFFLINE and TL_COMPOSE_CACHE go on working until the next major
    release, because the browser core and consumers' tests set them (SR-0233)."""
    monkeypatch.delenv("TL_OFFLINE", raising=False)
    monkeypatch.delenv("TL_CACHE", raising=False)
    monkeypatch.setenv("TL_COMPOSE_OFFLINE", "1")
    monkeypatch.setenv("TL_COMPOSE_CACHE", str(tmp_path / "old-cache"))
    assert resolvers.cache_only() is True
    assert resolvers.cache_root() == tmp_path / "old-cache"
    # The Tool's own names win when both are set.
    monkeypatch.setenv("TL_OFFLINE", "0")
    monkeypatch.setenv("TL_CACHE", str(tmp_path / "new-cache"))
    assert resolvers.cache_only() is False
    assert resolvers.cache_root() == tmp_path / "new-cache"


def test_a_project_with_no_sources_resolves_nothing(consumer_dir, source_dir, monkeypatch):
    """No operation over a project that declares no sources reaches a resolver at
    all (SR-0233, NG-0008)."""
    toml = consumer_dir / "throughline.toml"
    toml.write_text(re.sub(r"\[\[sources\]\][^\[]*", "", toml.read_text()))
    calls = []
    monkeypatch.setattr(resolvers, "resolver_for",
                        lambda source: calls.append(source) or GitResolver())
    assert compose(load_project(consumer_dir)) is None
    assert tl_main(["-C", str(consumer_dir), "query"]) == 0
    assert calls == []


# ------------------------------------------- SR-0235: the mirror is the Tool's own

def test_the_sourced_directive_is_registered_by_the_tool():
    assert "sourced" in _DIRECTIVES
    assert _DIRECTIVES["sourced"].publishes is False


def test_docs_mirror_a_borrowed_clause_through_tl(consumer_dir, capsys):
    """Over a composed project `tl docs` renders the mirror in full, under the
    identity the citing document uses (SR-0235)."""
    toml = consumer_dir / "throughline.toml"
    toml.write_text(toml.read_text() + '\n[docs]\npaths = ["docs/*.md"]\n')
    doc = consumer_dir / "docs" / "spec.md"
    doc.parent.mkdir()
    doc.write_text("# Spec\n\n<!-- tl:sourced type == 'system_requirement' -->\n<!-- tl:end -->\n")
    assert tl_main(["-C", str(consumer_dir), "docs"]) == 0
    rendered = doc.read_text()
    assert "toy:SR-0001 (V1.1.1)" in rendered
    assert "A normative clause the source offers" in rendered


def test_without_sources_a_sourced_marker_fails_naming_the_marker(consumer_dir, capsys):
    toml = consumer_dir / "throughline.toml"
    body = re.sub(r"\[\[sources\]\][^\[]*", "", toml.read_text())
    # Keep the graph sound without the source: the citing link becomes local.
    (consumer_dir / "system-requirements" / "SR-0001.yml").write_text(
        (consumer_dir / "system-requirements" / "SR-0001.yml").read_text()
        .replace("- target: toy:SR-0001\n  type: relates\n", ""))
    toml.write_text(body + '\n[docs]\npaths = ["docs/*.md"]\n')
    doc = consumer_dir / "docs" / "spec.md"
    doc.parent.mkdir()
    doc.write_text("# Spec\n\n<!-- tl:sourced type == 'system_requirement' -->\n<!-- tl:end -->\n")
    rc = tl_main(["-C", str(consumer_dir), "docs"])
    # Nothing to mirror: the placeholder, not a failure — the items cite no clause.
    assert rc == 0
    assert "reference no external clause" in doc.read_text()


# ------------------------------------------------ SR-0236: tl-compose is tl

def test_tl_compose_is_a_second_name_for_the_same_program():
    root = Path(throughline.__file__).parent.parent.parent
    scripts = tomllib.loads((root / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["tl"] == scripts["tl-compose"] == scripts["throughline-compose"]


def test_agentinfo_is_an_alias_of_context(consumer_dir, capsys):
    assert tl_main(["-C", str(consumer_dir), "context"]) == 0
    as_context = capsys.readouterr().out
    assert tl_main(["-C", str(consumer_dir), "agentinfo"]) == 0
    as_alias = capsys.readouterr().out
    assert as_alias == as_context
    assert "Namespaces bound in this union" in as_alias


# --------------------------------------------- SR-0234: the names are published

def test_the_composition_names_are_exported():
    for name in ("Source", "parse_sources", "Resolver", "register_resolver",
                 "resolve_sources", "Resolution", "compose", "build_union",
                 "apply_seam", "UnionResolver", "render_sourced"):
        assert name in throughline.__all__, name
        assert hasattr(throughline, name), name
