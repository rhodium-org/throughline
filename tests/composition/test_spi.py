# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Source-resolver SPI tests (SR-0011).

The interface is: given a source's coordinates and pin, return its items as a
throughline graph plus a fingerprint of what was read. Here we prove the reference
git resolver honours that contract, that resolver selection dispatches correctly,
that an unhandled source fails fast in the composer's vocabulary, and that a
connector-style custom resolver plugs in without touching the engine (UR-0004).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from throughline.storage import read_project

from throughline import resolvers as spi
from throughline.resolvers import GitResolver
from throughline.sources import Source
from throughline.resolvers import (
    ResolvedSource,
    Resolver,
    ResolverError,
    content_fingerprint,
    register_resolver,
    resolver_for,
)

_TOML = '[project]\nname = "src"\nformat_version = 2\n'


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("TL_CACHE", str(tmp_path / "cache"))


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    """Each test gets the default registry (git reference resolver only), so a
    custom resolver a test registers never leaks into another test."""
    monkeypatch.setattr(spi, "_REGISTRY", [GitResolver()])


def _source_dir(tmp_path: Path) -> Path:
    root = tmp_path / "local"
    root.mkdir()
    (root / "throughline.toml").write_text(_TOML)
    return root


def test_git_resolver_returns_project_and_fingerprint(tmp_path):
    _source_dir(tmp_path)
    rs = resolver_for(Source(namespace="s", path="local")).resolve(
        Source(namespace="s", path="local"), tmp_path)
    assert isinstance(rs, ResolvedSource)
    assert rs.project.config["project"]["name"] == "src"
    assert rs.fingerprint.startswith("sha256:")


def test_reference_resolver_handles_path_and_url():
    git = GitResolver()
    assert git.handles(Source(namespace="s", path="local"))
    assert git.handles(Source(namespace="s", url="https://x/y.git", ref="v1"))


def test_resolver_for_selects_the_reference_resolver():
    assert isinstance(resolver_for(Source(namespace="s", path="local")), GitResolver)


def test_unhandled_source_fails_fast(monkeypatch):
    # A registry with a resolver that claims nothing => resolver_for raises.
    class _Nothing(Resolver):
        def handles(self, source): return False
        def resolve(self, source, consumer_root): ...  # pragma: no cover
    monkeypatch.setattr(spi, "_REGISTRY", [_Nothing()])
    with pytest.raises(ResolverError, match="no resolver handles source 's'"):
        resolver_for(Source(namespace="s", path="local"))


def test_missing_path_fails_with_resolver_error(tmp_path):
    with pytest.raises(ResolverError, match="does not exist"):
        resolver_for(Source(namespace="s", path="nope")).resolve(
            Source(namespace="s", path="nope"), tmp_path)


def test_fingerprint_is_reproducible_and_content_sensitive(tmp_path):
    root = _source_dir(tmp_path)
    (root / "system-requirements").mkdir()
    (root / "system-requirements" / ".register.yml").write_text("prefix: SR\ndigits: 4\n")
    item = root / "system-requirements" / "SR-0001.yml"
    item.write_text(
        "uid: SR-0001\ntype: system_requirement\nstatus: approved\n"
        "title: A clause\ntext: The source shall do one thing.\n")
    first = content_fingerprint(read_project(root))
    # Re-reading the same edition yields the same fingerprint (SR-0012).
    assert content_fingerprint(read_project(root)) == first
    # A real change to normative content moves it.
    item.write_text(
        "uid: SR-0001\ntype: system_requirement\nstatus: approved\n"
        "title: A clause\ntext: The source shall do something different.\n")
    assert content_fingerprint(read_project(root)) != first


def test_custom_resolver_plugs_in_ahead_of_git(tmp_path):
    """A connector-style resolver claims its own sources and is consulted before
    the git catch-all — adding an authority needs no engine change (UR-0004)."""
    sentinel = read_project(_source_dir(tmp_path))

    class MemoryResolver(Resolver):
        def handles(self, source): return source.namespace == "mem"
        def resolve(self, source, consumer_root):
            return ResolvedSource(project=sentinel, fingerprint="sha256:memfixed")

    register_resolver(MemoryResolver())  # first=True by default
    chosen = resolver_for(Source(namespace="mem", path="ignored"))
    assert isinstance(chosen, MemoryResolver)
    rs = chosen.resolve(Source(namespace="mem", path="ignored"), tmp_path)
    assert rs.fingerprint == "sha256:memfixed"
    # A non-'mem' source still falls through to the git reference resolver.
    assert isinstance(resolver_for(Source(namespace="s", path="local")), GitResolver)
