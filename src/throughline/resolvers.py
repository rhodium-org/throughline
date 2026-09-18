# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Reaching a declared source: the resolver interface, its registry, and the
reference implementation over git and the file system (SR-0232, SR-0233).

The Tool composes a consumer's graph with items borrowed from other sources
(SR-0230). *How* a source's items are obtained — a git repository fetched at a
tag, a directory beside the consumer, or an authority read over its API by a
connector — is hidden behind one small contract, so that composition,
union-building, validation and drift detection never depend on a source's
origin (throughline-compose SR-0011):

    given a source's coordinates and a pin (the edition to read),
    return that source's items projected as a throughline graph,
    together with a fingerprint of what was read.

Reaching a source is work only the host can do, so it is taken as a parameter
with a default (SR-0226, SR-0232): every function that resolves accepts a
``resolver``, and the default consults the registry below, where the reference
:class:`GitResolver` is registered last as the catch-all for ``path`` and ``url``
sources. A connector package registers its own :class:`Resolver` ahead of it; a
caller with no subprocess and no network — a browser — passes its own resolver,
or the sources it has already staged, and obtains the same union. Resolvers only
ever *read* their sources; writing back to an authority is not composition's
(NG-0008).

The network is reached only for a ``url`` source the project declares, and only
when the cache cannot answer (SR-0233): a ``url`` + ``ref`` source is fetched from
its origin at the pinned ref into a per-user cache keyed by origin and ref, kept
*outside* any project tree so a consumer's own item scan never ingests a
resolved source. A cached source is reused rather than cloned again, but reuse is
not blind (throughline-compose SR-0043): a ref that is a commit id names one
commit for all time and is reused with no network access; a tag or a branch is a
name the origin can move, so the ref is looked up on the origin and the source
refetched only when it now points somewhere else. The cache-only switch turns the
look-up off and composes from the cache as it stands; a cold cache in that mode is
a refusal, never a fetch.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .fingerprint import fingerprint
from .model import Project
from .sources import Source
from .storage import ProjectError, read_project

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")
_SHA_RE = re.compile(r"\A[0-9a-fA-F]{7,40}\Z")

# The cache-only switch and the cache location, as environment variables for the
# command line (SR-0233). The names throughline-compose 0.21.0 read are honoured
# until the next major release, because the browser core and consumers' tests set
# them; the Tool's own names are read first.
OFFLINE_ENV = "TL_OFFLINE"
LEGACY_OFFLINE_ENV = "TL_COMPOSE_OFFLINE"
CACHE_ENV = "TL_CACHE"
LEGACY_CACHE_ENV = "TL_COMPOSE_CACHE"


class ResolverError(Exception):
    """A source could not be resolved — a bad path, a git fetch that failed, an
    authority with no resolver, or a host that cannot run git. Fail fast
    (throughline-compose SR-0005), in the Tool's own words (SR-0232)."""


@dataclass(frozen=True)
class ResolvedSource:
    """What a resolver returns for one source: the source's items projected as a
    throughline :class:`~throughline.model.Project`, plus a ``fingerprint`` of the
    exact edition that was read. The fingerprint is a stable digest of the resolved
    graph's normative content, so the same coordinates at the same pin resolve to
    the same fingerprint run to run and machine to machine (throughline-compose
    SR-0012), whatever authority produced the graph."""

    project: Project
    fingerprint: str


class Resolver(ABC):
    """The one interface a source is reached through (SR-0232).

    A concrete resolver claims the sources it understands with :meth:`handles` and
    turns a claimed source into a :class:`ResolvedSource` with :meth:`resolve`. The
    reference implementation covers ``path`` and ``url`` (git) sources; a connector
    package subclasses this to reach a non-git authority and calls
    :func:`register_resolver` at import time; a browser passes an instance straight
    to :func:`throughline.resolve_sources`. No other code path fetches a source."""

    @abstractmethod
    def handles(self, source: Source) -> bool:
        """True when this resolver knows how to reach ``source``."""

    @abstractmethod
    def resolve(self, source: Source, consumer_root: Path) -> ResolvedSource:
        """Fetch ``source`` at its pinned edition and return it as a
        :class:`ResolvedSource`. ``consumer_root`` locates a ``path`` source
        relative to the consumer project. Raises :class:`ResolverError` on any
        failure to reach or read the source."""


# Registered resolvers, consulted in order. The reference git resolver registers
# itself last (as a catch-all for path/url sources); a more specific authority
# resolver registered by a connector is consulted first and claims its own sources.
_REGISTRY: list[Resolver] = []


def register_resolver(resolver: Resolver, *, first: bool = True) -> Resolver:
    """Add ``resolver`` to the registry and return it (usable as a decorator on a
    subclass instance). Connector resolvers register ``first=True`` (the default)
    so an authority-specific resolver is consulted before the git catch-all; the
    reference resolver registers with ``first=False``."""
    if first:
        _REGISTRY.insert(0, resolver)
    else:
        _REGISTRY.append(resolver)
    return resolver


def resolver_for(source: Source) -> Resolver:
    """The registered resolver that handles ``source``, or a :class:`ResolverError`
    naming the source when none does — so an unreachable authority fails fast and
    reads in the composer's own vocabulary."""
    for resolver in _REGISTRY:
        if resolver.handles(source):
            return resolver
    raise ResolverError(
        f"no resolver handles source '{source.namespace}' — its authority has no "
        "registered resolver")


def content_fingerprint(project: Project) -> str:
    """A stable digest of a resolved source's normative content (throughline-compose
    SR-0011, SR-0012).

    Built from the core per-item :func:`~throughline.fingerprint.fingerprint` of
    every item, ordered by UID so the digest is independent of scan order. Two
    resolutions of the same edition yield the same value, and any real change to a
    borrowed item's normative content moves it — an authority-agnostic edition
    marker that a git commit hash or an issue-tracker revision id can stand behind."""
    schema = project.schema
    per_item = sorted(
        f"{item.uid}\x1f{fingerprint(item, schema)}" for item in project.items())
    digest = hashlib.sha256("\x1e".join(per_item).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


# ------------------------------------------------------------ the cache (SR-0233)

def _announce(msg: str) -> None:
    """Say what is being fetched, while it is being fetched (throughline-compose
    SR-0031).

    Standard error, so machine-readable output on stdout is unaffected. Only the
    cold-cache path calls this, which is what keeps it honest as a progress notice
    rather than noise: a run that prints nothing is a run that had nothing slow to
    do, and every run that does have a network fetch ahead of it says so first."""
    print(f"tl: {msg}", file=sys.stderr, flush=True)


def cache_root() -> Path:
    """The per-user source cache, outside any project tree (SR-0233).

    Honours ``TL_CACHE`` (and the name throughline-compose read, ``TL_COMPOSE_CACHE``)
    for tests and CI; otherwise ``XDG_CACHE_HOME`` or ``~/.cache``.
    """
    override = os.environ.get(CACHE_ENV) or os.environ.get(LEGACY_CACHE_ENV)
    if override:
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "throughline" / "sources"


def cache_only() -> bool:
    """Whether the environment asks to compose from the cache without asking any
    origin anything (SR-0233): ``TL_OFFLINE``, or the name throughline-compose read,
    ``TL_COMPOSE_OFFLINE``. The parameter form is ``resolve_source(...,
    cache_only=True)`` and :class:`GitResolver` ``(cache_only=True)``."""
    for name in (OFFLINE_ENV, LEGACY_OFFLINE_ENV):
        value = os.environ.get(name, "").strip().lower()
        if value:
            return value in {"1", "true", "yes", "on"}
    return False


def _slug(text: str) -> str:
    return _SLUG_RE.sub("-", text).strip("-") or "x"


def _cache_dir(url: str, ref: str) -> Path:
    # Key by (url, ref). A short hash guarantees uniqueness; a readable slug of the
    # url's last segment and the ref makes the directory legible on disk.
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    tail = _slug(url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git"))
    return cache_root() / f"{tail}-{digest}@{_slug(ref)}"


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run git, or refuse in the Tool's words saying which case it is (SR-0232).

    Git missing from the path and a platform that runs no subprocess at all are two
    different failures with two different remedies, so the sentence names the one
    that happened rather than offering a remedy for the other. ``FileNotFoundError``
    is the first; the plain ``OSError`` a host such as Pyodide raises is the second,
    and it used to escape uncaught (throughline-compose issue #5)."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True, text=True,
        )
    except FileNotFoundError as e:
        raise ResolverError(
            "git is not installed here; it is required to fetch url sources") from e
    except OSError as e:
        raise ResolverError(
            "this platform runs no subprocess, so git cannot fetch a url source "
            "here; supply a resolver, or stage the source and compose from the "
            f"cache ({OFFLINE_ENV}=1)") from e


def _fetch(url: str, ref: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".fetch-", dir=dest.parent))
    try:
        # --branch accepts a tag or branch name; a bare commit SHA needs a second
        # step, so fall back to a full clone + checkout when the pinned clone fails.
        r = _git("clone", "--depth", "1", "--branch", ref, url, str(tmp))
        if r.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            tmp = Path(tempfile.mkdtemp(prefix=".fetch-", dir=dest.parent))
            r = _git("clone", url, str(tmp))
            if r.returncode != 0:
                raise ResolverError(
                    f"could not clone {url}: {r.stderr.strip() or 'git clone failed'}")
            co = _git("checkout", ref, cwd=tmp)
            if co.returncode != 0:
                raise ResolverError(
                    f"ref '{ref}' not found in {url}: "
                    f"{co.stderr.strip() or 'git checkout failed'}")
        _publish(tmp, dest)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


def _publish(tmp: Path, dest: Path) -> None:
    """Swap a materialised checkout into place, leaving no window where it is absent.

    Deleting ``dest`` and then moving ``tmp`` onto it would be simpler and is wrong
    here: the cache is a per-user store shared by every project on the host, and a
    refetch happens on any run whose ref has moved — so a second process is quite
    likely to be reading ``dest`` exactly while it is replaced. On self-hosted
    runners that share one cache across many composition gates, that is a routine
    collision rather than a theoretical one.

    Two renames within a single directory avoid it. A reader sees either the old tree
    or the new one and never a partial or missing one, and a reader that already
    opened the old tree keeps a valid tree for as long as it holds it, because the
    unlink happens only after the directory is out of the way. If the swap itself
    fails the old checkout goes back, so a failed refetch costs nothing.
    """
    stale = dest.with_name(f".stale-{uuid4().hex[:8]}-{dest.name}")
    displaced = dest.exists()
    if displaced:
        os.replace(dest, stale)
    try:
        os.replace(tmp, dest)
    except BaseException:
        if displaced:
            os.replace(stale, dest)
        raise
    if displaced:
        shutil.rmtree(stale, ignore_errors=True)


def _head(dest: Path) -> str:
    """The commit checked out in a cached source, or "" if it cannot be read."""
    r = _git("rev-parse", "HEAD", cwd=dest)
    return r.stdout.strip() if r.returncode == 0 else ""


def _names_commit(ref: str, head: str) -> bool:
    """Whether ``ref`` is the commit id of ``head`` — abbreviated or in full.

    Matching against the cached head, rather than testing the ref for hex on its
    own, is what makes this safe: a tag legitimately named like an abbreviated sha
    fails the comparison and is revalidated as the name it is."""
    return bool(_SHA_RE.match(ref)) and head.startswith(ref.lower())


def _remote_commit(url: str, ref: str) -> str:
    """The commit ``ref`` currently points to at ``url`` (throughline-compose
    SR-0043).

    Annotated tags are peeled. git reports both ``refs/tags/x`` and
    ``refs/tags/x^{}`` for one such tag, and only the second is the commit — the
    first is the tag object, which never equals a cached checkout's HEAD and would
    otherwise make every run look like a moved ref.
    """
    if ref.startswith("refs/"):
        patterns = [ref, f"{ref}^{{}}"]
    else:
        # Fully-qualified rather than bare, so the pattern cannot also match an
        # unrelated ref that merely ends in the same path component.
        patterns = [f"refs/heads/{ref}", f"refs/tags/{ref}", f"refs/tags/{ref}^{{}}"]
    r = _git("ls-remote", url, *patterns)
    if r.returncode != 0:
        raise ResolverError(
            f"could not reach {url} to check whether ref '{ref}' has moved: "
            f"{r.stderr.strip() or 'git ls-remote failed'}. Set {OFFLINE_ENV}=1 to "
            "compose from the cache without checking.")
    found: dict[str, str] = {}
    for line in r.stdout.splitlines():
        sha, _, name = line.partition("\t")
        name = name.strip()
        if not name:
            continue
        peeled = name.endswith("^{}")
        base = name[:-3] if peeled else name
        if peeled or base not in found:
            found[base] = sha.strip()
    if not found:
        raise ResolverError(f"ref '{ref}' no longer exists in {url}")
    if len(found) > 1:
        raise ResolverError(
            f"ref '{ref}' is ambiguous in {url} — it matches "
            f"{', '.join(sorted(found))}. Qualify it as a full refs/… path.")
    return next(iter(found.values()))


def _revalidate(source: Source, dest: Path, *, offline: bool) -> None:
    """Refetch a cached source whose ref has moved since it was fetched
    (throughline-compose SR-0043)."""
    assert source.url is not None and source.ref is not None
    head = _head(dest)
    if head and _names_commit(source.ref, head):
        return
    if offline:
        return
    current = _remote_commit(source.url, source.ref)
    if current == head:
        return
    _announce(f"source '{source.namespace}' ref {source.ref} moved "
              f"{head[:9] or 'unknown'} -> {current[:9]} — refetching")
    _fetch(source.url, source.ref, dest)
    _announce(f"resolved source '{source.namespace}'")


def _descend(root: Path, source: Source) -> Path:
    """Apply an optional ``subdir`` (throughline-compose SR-0008), returning the
    project directory.

    ``subdir`` is validated relative and non-escaping at parse time; here we also
    confirm it stays within ``root`` after resolution (defence in depth against a
    symlink in the fetched tree) and that it is itself a throughline project.
    """
    if not source.subdir:
        return root
    target = (root / source.subdir).resolve()
    root_resolved = root.resolve()
    if root_resolved != target and root_resolved not in target.parents:
        raise ResolverError(
            f"source '{source.namespace}' subdir '{source.subdir}' escapes the "
            "source root")
    if not target.is_dir():
        raise ResolverError(
            f"source '{source.namespace}' subdir '{source.subdir}' does not exist")
    return target


def resolve_source(source: Source, consumer_root: Path, *,
                   cache_only: bool | None = None) -> Path:
    """Return the local directory a source composes from (SR-0233).

    ``path`` sources resolve relative to ``consumer_root`` and touch neither git nor
    the network; ``url`` sources are fetched (once) into the per-user cache. An
    optional ``subdir`` then selects the throughline project within that tree.
    ``cache_only`` is the switch as a parameter; ``None`` reads it from the
    environment (:func:`cache_only`).
    """
    if not source.is_remote:
        assert source.path is not None
        local = (consumer_root / source.path).resolve()
        if not local.is_dir():
            raise ResolverError(
                f"source '{source.namespace}' path does not exist: {local}")
        project = _descend(local, source)
        if not (project / "throughline.toml").is_file():
            raise ResolverError(
                f"source '{source.namespace}' at {project} is not a throughline "
                "project (no throughline.toml)")
        return project

    offline = globals()["cache_only"]() if cache_only is None else cache_only
    assert source.url is not None and source.ref is not None
    dest = _cache_dir(source.url, source.ref)
    if dest.is_dir() and (dest / ".git").exists():
        _revalidate(source, dest, offline=offline)
    else:
        if offline:
            # Cache-only has to mean it. Fetching here because the cache happens to
            # be cold would make the switch a preference rather than a guarantee,
            # and the caller who set it is precisely the one who cannot fetch.
            raise ResolverError(
                f"source '{source.namespace}' is not in the cache and "
                f"{OFFLINE_ENV} forbids fetching it: {source.url}@{source.ref}. "
                f"Unset {OFFLINE_ENV} to fetch it once, then set it again.")
        if dest.exists():  # partial/corrupt leftover
            shutil.rmtree(dest, ignore_errors=True)
        # The clone is the only slow thing composition does, and it happens before
        # any checking has begun — so a first run against an unfetched source used
        # to sit silent for the whole of it. Announced before, not after.
        _announce(f"resolving source '{source.namespace}' from "
                  f"{source.url}@{source.ref} (not cached — fetching)")
        _fetch(source.url, source.ref, dest)
        _announce(f"resolved source '{source.namespace}'")
    project = _descend(dest, source)
    if not (project / "throughline.toml").is_file():
        loc = f"{source.url}@{source.ref}"
        if source.subdir:
            loc += f" (subdir '{source.subdir}')"
        raise ResolverError(
            f"source '{source.namespace}' at {loc} is not a throughline project "
            "(no throughline.toml)")
    return project


# --------------------------------------------- the reference resolver (SR-0232)

class GitResolver(Resolver):
    """Resolve ``path`` and ``url`` (git) sources — the reference resolver, and the
    default of the resolver parameter (SR-0226, SR-0232).

    A thin adapter: the fetch and cache mechanics are :func:`resolve_source`, the
    graph loading is the Tool's tolerant read-only reader, and the result is wrapped
    in a :class:`ResolvedSource` with a content fingerprint of the edition read.
    ``cache_only`` fixes the switch for this instance; ``None`` reads it from the
    environment on each resolution."""

    def __init__(self, *, cache_only: bool | None = None) -> None:
        self._cache_only = cache_only

    def handles(self, source: Source) -> bool:
        # Every declared source is a path or a url today; an authority resolver
        # registered by a connector claims its own sources ahead of this catch-all.
        return source.path is not None or source.url is not None

    def resolve(self, source: Source, consumer_root: Path) -> ResolvedSource:
        src_dir = resolve_source(source, consumer_root, cache_only=self._cache_only)
        try:
            # A source is read-only: load it through the tolerant multi-major reader
            # (throughline-compose SR-0017), so a source pinned at an older on-disk
            # major composes without being forced to migrate first. The strict
            # single-major gate applies only to the consumer project being operated
            # on, never here.
            project = read_project(src_dir)
        except ProjectError as e:
            where = f"{source.url}@{source.ref}" if source.is_remote else source.path
            raise ResolverError(
                f"source '{source.namespace}' at {where}: {e}") from e
        return ResolvedSource(
            project=project, fingerprint=content_fingerprint(project))


# The reference resolver is the catch-all: registered last so a connector's
# authority-specific resolver is consulted first (register_resolver default).
register_resolver(GitResolver(), first=False)
