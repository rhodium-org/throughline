# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Resolver tests (SR-0006): local paths, and url sources fetched by pinned ref
into a cache outside the project tree. A throwaway local git repo stands in for a
remote origin — git clones a filesystem path exactly as it would a URL."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from throughline.resolvers import ResolverError, _remote_commit, resolve_source
from throughline.sources import Source

_TOML = '[project]\nname = "src"\nformat_version = 2\n'


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=str(cwd), check=True, capture_output=True, text=True)


def _origin(tmp_path: Path, tag: str = "v4.0.3", subdir: str | None = None) -> Path:
    """A minimal throughline project in a git repo, tagged like a source edition.

    When ``subdir`` is given the throughline.toml lives under that subpath, standing
    in for a graph inside a larger application repo (SR-0008)."""
    repo = tmp_path / "origin"
    repo.mkdir()
    _git("init", "-b", "main", cwd=repo)
    proj = repo / subdir if subdir else repo
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "throughline.toml").write_text(_TOML)
    _git("add", ".", cwd=repo)
    _git("commit", "-m", "edition", cwd=repo)
    _git("tag", tag, cwd=repo)
    return repo


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("TL_CACHE", str(tmp_path / "cache"))


def test_local_path_resolves(tmp_path):
    src = tmp_path / "local"
    src.mkdir()
    (src / "throughline.toml").write_text(_TOML)
    got = resolve_source(Source(namespace="s", path="local"), tmp_path)
    assert got == src.resolve()


def test_local_path_missing_fails(tmp_path):
    with pytest.raises(ResolverError, match="does not exist"):
        resolve_source(Source(namespace="s", path="nope"), tmp_path)


def test_url_ref_fetched_into_cache(tmp_path):
    origin = _origin(tmp_path)
    src = Source(namespace="asvs", url=str(origin), ref="v4.0.3")
    got = resolve_source(src, tmp_path)
    # Materialised outside the consumer tree, with the source's content present.
    assert (got / "throughline.toml").is_file()
    assert tmp_path / "consumer" not in got.parents
    assert str(tmp_path / "cache") in str(got)


def test_url_ref_is_idempotent(tmp_path):
    origin = _origin(tmp_path)
    src = Source(namespace="asvs", url=str(origin), ref="v4.0.3")
    first = resolve_source(src, tmp_path)
    marker = first / "RESOLVED_ONCE"
    marker.write_text("x")  # survives only if the second call does not refetch
    second = resolve_source(src, tmp_path)
    assert second == first
    assert marker.is_file()


def test_bad_ref_fails_fast(tmp_path):
    origin = _origin(tmp_path)
    src = Source(namespace="asvs", url=str(origin), ref="v9.9.9")
    with pytest.raises(ResolverError):
        resolve_source(src, tmp_path)


def test_url_subdir_resolves_to_subproject(tmp_path):  # SR-0008
    origin = _origin(tmp_path, subdir="requirements")
    src = Source(namespace="console", url=str(origin), ref="v4.0.3",
                 subdir="requirements")
    got = resolve_source(src, tmp_path)
    assert got.name == "requirements"
    assert (got / "throughline.toml").is_file()


def test_url_subdir_missing_fails(tmp_path):  # SR-0008
    origin = _origin(tmp_path, subdir="requirements")
    src = Source(namespace="console", url=str(origin), ref="v4.0.3",
                 subdir="nope")
    with pytest.raises(ResolverError, match="does not exist"):
        resolve_source(src, tmp_path)


def test_url_subdir_without_toml_fails(tmp_path):  # SR-0008
    # A repo whose root IS a project, but the named subdir is not a throughline dir.
    origin = _origin(tmp_path)
    (origin / "docs").mkdir()
    (origin / "docs" / "README.md").write_text("# docs\n")
    _git("add", ".", cwd=origin)
    _git("commit", "-m", "docs", cwd=origin)
    _git("tag", "v5", cwd=origin)
    src = Source(namespace="console", url=str(origin), ref="v5", subdir="docs")
    with pytest.raises(ResolverError, match="not a throughline project"):
        resolve_source(src, tmp_path)


def test_local_path_subdir_resolves(tmp_path):  # SR-0008
    root = tmp_path / "app"
    (root / "requirements").mkdir(parents=True)
    (root / "requirements" / "throughline.toml").write_text(_TOML)
    got = resolve_source(
        Source(namespace="console", path="app", subdir="requirements"), tmp_path)
    assert got == (root / "requirements").resolve()


# --- SR-0043: a ref that is a name is re-checked before the cache is reused ----

def _head_of(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo),
        capture_output=True, text=True, check=True).stdout.strip()


def _add_commit(repo: Path, marker: str) -> str:
    """A second edition of the source, so a moved ref has somewhere to move to."""
    (repo / "EDITION").write_text(marker)
    _git("add", ".", cwd=repo)
    _git("commit", "-m", marker, cwd=repo)
    return _head_of(repo)


def test_moved_tag_is_refetched(tmp_path):
    origin = _origin(tmp_path)
    src = Source(namespace="asvs", url=str(origin), ref="v4.0.3")
    first = resolve_source(src, tmp_path)
    assert not (first / "EDITION").exists()

    _add_commit(origin, "second")
    _git("tag", "-f", "v4.0.3", cwd=origin)  # the estate repoints tags in place

    again = resolve_source(src, tmp_path)
    assert again == first
    assert (again / "EDITION").read_text() == "second"


def test_moved_branch_is_refetched(tmp_path):
    origin = _origin(tmp_path)
    src = Source(namespace="console", url=str(origin), ref="main")
    resolve_source(src, tmp_path)
    _add_commit(origin, "advanced")
    got = resolve_source(src, tmp_path)
    assert (got / "EDITION").read_text() == "advanced"


def test_unmoved_tag_is_not_refetched(tmp_path):
    """Revalidation must cost a ref lookup, never a clone, when nothing moved."""
    origin = _origin(tmp_path)
    src = Source(namespace="asvs", url=str(origin), ref="v4.0.3")
    first = resolve_source(src, tmp_path)
    marker = first / "NOT_REFETCHED"
    marker.write_text("x")  # a clone would discard this
    assert resolve_source(src, tmp_path) == first
    assert marker.is_file()


def test_annotated_tag_peels_to_its_commit(tmp_path):
    """An annotated tag's own object id is not the commit, and comparing against
    it would refetch on every single run."""
    origin = _origin(tmp_path, tag="lightweight")
    _git("tag", "-a", "v5.0.0", "-m", "edition v5", cwd=origin)
    src = Source(namespace="asvs", url=str(origin), ref="v5.0.0")
    first = resolve_source(src, tmp_path)
    marker = first / "NOT_REFETCHED"
    marker.write_text("x")
    resolve_source(src, tmp_path)
    assert marker.is_file()


def test_remote_commit_peels_an_annotated_tag(tmp_path):
    """The tag object's id is not the commit. Comparing a cached HEAD against the
    unpeeled value never matches, so every run would refetch."""
    origin = _origin(tmp_path, tag="lightweight")
    _git("tag", "-a", "v5.0.0", "-m", "edition v5", cwd=origin)
    tag_object = subprocess.run(
        ["git", "rev-parse", "v5.0.0"], cwd=str(origin),
        capture_output=True, text=True, check=True).stdout.strip()
    commit = _head_of(origin)
    assert tag_object != commit, "fixture must produce a real annotated tag"
    assert _remote_commit(str(origin), "v5.0.0") == commit


def test_commit_id_ref_never_contacts_the_origin(tmp_path):
    origin = _origin(tmp_path)
    sha = _head_of(origin)
    src = Source(namespace="asvs", url=str(origin), ref=sha)
    first = resolve_source(src, tmp_path)
    # With the origin gone there is no network at all; a commit id must not need one.
    origin.rename(tmp_path / "origin-moved-away")
    assert resolve_source(src, tmp_path) == first


def test_abbreviated_commit_id_ref_never_contacts_the_origin(tmp_path):
    origin = _origin(tmp_path)
    src = Source(namespace="asvs", url=str(origin), ref=_head_of(origin)[:7])
    first = resolve_source(src, tmp_path)
    origin.rename(tmp_path / "origin-moved-away")
    assert resolve_source(src, tmp_path) == first


def test_unreachable_origin_fails_and_names_the_offline_switch(tmp_path):
    origin = _origin(tmp_path)
    src = Source(namespace="asvs", url=str(origin), ref="v4.0.3")
    resolve_source(src, tmp_path)
    origin.rename(tmp_path / "origin-moved-away")
    with pytest.raises(ResolverError, match="TL_OFFLINE"):
        resolve_source(src, tmp_path)


def test_offline_mode_composes_from_the_cache_unchecked(tmp_path, monkeypatch):
    origin = _origin(tmp_path)
    src = Source(namespace="asvs", url=str(origin), ref="v4.0.3")
    first = resolve_source(src, tmp_path)
    _add_commit(origin, "second")
    _git("tag", "-f", "v4.0.3", cwd=origin)

    monkeypatch.setenv("TL_OFFLINE", "1")
    again = resolve_source(src, tmp_path)
    assert again == first
    assert not (again / "EDITION").exists()  # deliberately stale, because asked for


def test_deleted_ref_fails_rather_than_reusing_the_cache(tmp_path):
    origin = _origin(tmp_path)
    src = Source(namespace="asvs", url=str(origin), ref="v4.0.3")
    resolve_source(src, tmp_path)
    _git("tag", "-d", "v4.0.3", cwd=origin)
    with pytest.raises(ResolverError, match="no longer exists"):
        resolve_source(src, tmp_path)


def test_ambiguous_ref_fails_rather_than_being_guessed(tmp_path):
    origin = _origin(tmp_path)
    src = Source(namespace="asvs", url=str(origin), ref="v4.0.3")
    resolve_source(src, tmp_path)
    _git("branch", "v4.0.3", cwd=origin)  # now both a tag and a branch
    with pytest.raises(ResolverError, match="ambiguous"):
        resolve_source(src, tmp_path)


def test_offline_mode_with_a_cold_cache_fails_rather_than_fetching(tmp_path, monkeypatch):
    """Cache-only has to mean it (SR-0043).

    Fetching anyway when the cache happens to be cold would make the switch a
    preference rather than a guarantee — and the caller who sets it is exactly the
    one with no route to the origin, so the 'convenience' is a hang or a timeout
    instead of a sentence saying what is wrong."""
    origin = _origin(tmp_path)
    src = Source(namespace="asvs", url=str(origin), ref="v4.0.3")
    monkeypatch.setenv("TL_OFFLINE", "1")
    with pytest.raises(ResolverError, match="not in the cache"):
        resolve_source(src, tmp_path)


def test_a_failed_refetch_leaves_the_previous_edition_in_place(tmp_path, monkeypatch):
    """A refetch that cannot complete must cost nothing.

    While refetching never happened this was unreachable; now that a moved ref
    triggers one on an ordinary run, a clone that fails part-way must not be able to
    leave a consumer with no cache at all."""
    from throughline import resolvers as R

    origin = _origin(tmp_path)
    src = Source(namespace="asvs", url=str(origin), ref="v4.0.3")
    first = resolve_source(src, tmp_path)
    (first / "PREVIOUS").write_text("keep me")
    _add_commit(origin, "second")
    _git("tag", "-f", "v4.0.3", cwd=origin)

    real_git = R._git

    def no_clone(*args, **kwargs):
        if args and args[0] == "clone":
            return subprocess.CompletedProcess(args, 1, "", "network is down")
        return real_git(*args, **kwargs)

    monkeypatch.setattr(R, "_git", no_clone)
    with pytest.raises(ResolverError):
        resolve_source(src, tmp_path)

    assert (first / "PREVIOUS").read_text() == "keep me"
    assert not (first / "EDITION").exists()
    assert not [p for p in first.parent.iterdir() if p.name.startswith(".stale-")]


def test_publish_puts_the_old_checkout_back_if_the_swap_fails(tmp_path, monkeypatch):
    """The swap is two renames, and the second one can still fail — a full disk, a
    cache pulled out from under the run. When it does, the checkout that was already
    there goes back, rather than being left parked under a hidden name where nothing
    would ever find it again."""
    from throughline import resolvers as R

    dest = tmp_path / "cached"
    dest.mkdir()
    (dest / "which").write_text("old")
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    (incoming / "which").write_text("new")

    real_replace = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:  # dest is aside; fail bringing the new tree in
            raise OSError("no space left on device")
        return real_replace(src, dst)

    monkeypatch.setattr(R.os, "replace", flaky)
    with pytest.raises(OSError):
        R._publish(incoming, dest)

    assert (dest / "which").read_text() == "old"
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".stale-")]


def test_publish_leaves_no_residue_behind(tmp_path):
    """A refetch on a shared cache must not accumulate hidden copies of every
    superseded edition — the store is per-user and long-lived."""
    from throughline import resolvers as R

    dest = tmp_path / "cached"
    dest.mkdir()
    (dest / "which").write_text("old")
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    (incoming / "which").write_text("new")

    R._publish(incoming, dest)

    assert (dest / "which").read_text() == "new"
    assert [p.name for p in tmp_path.iterdir()] == ["cached"]
