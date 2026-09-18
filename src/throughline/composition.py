# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Composing a project with the sources it declares (SR-0230).

A project that declares ``[[sources]]`` in its ``throughline.toml`` is worked as
one graph: each declared source, and every source those sources declare, to any
depth, is resolved through the resolver interface (SR-0232) and folded into one
union under the consumer's schema (:mod:`throughline.union`); the unchanged
validator checks the union and the seam decides which findings the consumer sees
(:mod:`throughline.seam`); a borrowed item is named ``namespace:UID`` in
everything the Tool reports. This module holds the resolution of the closure, the
summary ``check`` prints over a union, the two document-side pieces — the target
resolver a composed document renders through and the ``tl:sourced`` mirror
(SR-0235) — and the composition section of the agent brief. The command line's
union-aware commands are its callers (SR-0225).

The rules are those throughline-compose 0.21.0 ratified in its own graph and are
not redecided here (SR-0231): importer-chosen namespaces (throughline-compose
SR-0001), source-native UIDs (SR-0002), a pinned origin and a per-user cache
(SR-0006), a graph in a subdirectory (SR-0008), a namespace bound to two editions
refused (SR-0015), a source that composes what it composes (SR-0045), and a union
the unchanged validator checks (SR-0004).
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .brief import _by_count
from .graph import Index
from .inject import InjectError, TargetResolver, matching, register_directive, render_item
from .links import GroundingView
from .model import Project
from .resolvers import (
    ResolvedSource,
    Resolver,
    ResolverError,
    cache_root,
    resolver_for,
)
from .seam import is_borrowed
from .sources import Source, SourceError, parse_sources, withdrawn_declarations
from .union import ComposeError, Union, build_union
from .validate import is_namespace_qualified
from .views import check_summary

# ------------------------------------------------------ resolving the closure


def source_location(s: Source) -> str:
    """A human-readable origin for a source, for summaries and conflict messages."""
    return f"{s.url}@{s.ref}" if s.is_remote else f"path {s.path}"


def _chain(via: tuple[str, ...]) -> str:
    """The path that carried a namespace into the union, as the summary prints it."""
    return " › ".join(via)


def _origin(where: str, via: tuple[str, ...]) -> str:
    """Where a binding came from (throughline-compose SR-0015): its location and the
    chain of declared sources that carried it in, or the consumer's own
    declaration."""
    return f"{where} via {_chain(via)}" if via else f"{where}, declared by you"


def _conflict_message(ns: str, origin_a: str, fp_a: str,
                      origin_b: str, fp_b: str) -> str:
    """The advisory a two-edition namespace collision fails with (throughline-compose
    SR-0015): the why (namespace, both editions, the path each came by) and the fix
    (pin explicitly, or alias apart on the source carrying one of them) — never a
    suggestion to merge, which the model cannot honour."""
    short = lambda fp: fp.removeprefix("sha256:")[:12]  # noqa: E731
    return (
        f"namespace '{ns}' is bound to two different editions and tl will "
        f"neither merge them nor pick one for you:\n"
        f"  - {origin_a} [{short(fp_a)}]\n"
        f"  - {origin_b} [{short(fp_b)}]\n"
        f"fix it by either: pinning '{ns}' explicitly in your own [[sources]] to the "
        f"single edition you intend, so one binding governs every reference to it; "
        f"or aliasing one of them apart — set alias = {{ {ns} = \"{ns}-alt\" }} on "
        f"the declared source carrying it — so both editions compose side by side "
        f"as the two separate sources they are"
    )


def _resolve_with(resolver: Resolver | None, source: Source, root: Path) -> ResolvedSource:
    """Resolve one source through the supplied resolver, or through the registry
    when none was supplied (SR-0232)."""
    return (resolver if resolver is not None else resolver_for(source)).resolve(source, root)


class Resolution:
    """The outcome of resolving a consumer's sources and their closure
    (throughline-compose SR-0045): the namespace -> :class:`ResolvedSource` map in
    bind order, a human origin and the carrying path per bound namespace, the
    coordinates each was reached by, the label map each source's own references
    resolve through, and the notices the summary prints."""

    def __init__(self, resolver: Resolver | None = None):
        self.resolved: dict[str, ResolvedSource] = {}
        self.locations: dict[str, str] = {}
        # The declaration each bound namespace was reached by — the consumer's own
        # for a declared source, the inherited one for a transitive source, so an
        # export can state every pin as data and not only as prose.
        self.coordinates: dict[str, Source] = {}
        # The chain of union namespaces that carried each binding in; empty for a
        # source the consumer declared itself.
        self.via: dict[str, tuple[str, ...]] = {}
        # Per bound namespace: the labels that source's own references may use —
        # its direct declarations and every label its sources hoisted into it —
        # mapped to the union namespace each was bound under.
        self.labels: dict[str, dict[str, str]] = {}
        # What the summary should say beyond the bindings: a same-edition
        # coalescence, or a withdrawn key found in a published edition.
        self.notices: list[str] = []
        # fingerprint -> the first namespace bound to that edition
        self._holder: dict[str, str] = {}
        # One resolution per coordinates however many paths reach them
        self._memo: dict[tuple, ResolvedSource] = {}
        self._resolver = resolver

    def origin(self, ns: str) -> str:
        return _origin(self.locations[ns], self.via[ns])

    def bind(self, ns: str, rs: ResolvedSource, where: str, source: Source,
             via: tuple[str, ...] = ()) -> bool:
        """Bind ``ns`` to a resolved source and return True, or return False when
        ``ns`` is already bound to that same edition, or fail fast when it is
        bound to a different one."""
        existing = self.resolved.get(ns)
        if existing is not None:
            if existing.fingerprint != rs.fingerprint:
                raise ResolverError(_conflict_message(
                    ns, self.origin(ns), existing.fingerprint,
                    _origin(where, via), rs.fingerprint))
            return False  # same edition — a single bound source
        self.resolved[ns] = rs
        self.locations[ns] = where
        self.coordinates[ns] = source
        self.via[ns] = tuple(via)
        self.labels.setdefault(ns, {})
        self._holder.setdefault(rs.fingerprint, ns)
        return True

    def walk(self, ns: str, rename: Callable[[str], str]) -> None:
        """Bind the closure of the source bound as ``ns``, depth-first in its
        declared order.

        ``rename`` maps a label as it would appear in *that source's own* union to
        the namespace the consumer binds it under: the consumer's alias on the
        declared source, composed with each intermediate's own alias on the way
        down, so an alias applies throughout the subtree it was set on. A source
        already bound to the same edition under another label is not bound twice:
        the reference is folded into the existing binding and the summary says so.
        A source bound before is not walked again, which is also what ends a cycle.
        """
        rs = self.resolved[ns]
        carrier = self.coordinates[ns]
        via = self.via[ns] + (ns,)
        for dep_ns in withdrawn_declarations(rs.project):
            self.notices.append(
                f"'{ns}' ({self.locations[ns]}) declares 'reexport' on its source "
                f"'{dep_ns}', a key withdrawn by throughline-compose SR-0045 — "
                f"ignored; the sources it named are composed anyway")
        src_root = Path(rs.project.path)
        view = self.labels[ns]
        for d in parse_sources(rs.project, withdrawn="ignore"):
            if d.path is not None and carrier.is_remote:
                raise ResolverError(
                    f"source '{d.namespace}' is declared by '{ns}' with a path "
                    f"({d.path}), but '{ns}' was fetched by url "
                    f"({carrier.url}@{carrier.ref}) and a path resolves nowhere from "
                    f"the cache — a source published by url must pin its own sources "
                    f"by url + ref (reached via {_chain(via)})")
            resolved = self._resolve(d, src_root, via)
            union_name = rename(d.namespace)
            holder = self._holder.get(resolved.fingerprint)
            if holder is not None and holder != union_name:
                bound = holder
                self.notices.append(
                    f"'{union_name}' (via {_chain(via)}) is the same edition as "
                    f"'{holder}' ({self.origin(holder)}) — bound once as '{holder}'")
            else:
                bound = union_name
                if self.bind(union_name, resolved, source_location(d), d, via):
                    self.walk(union_name,
                              lambda n, _r=rename, _a=d.alias: _r(_a.get(n, n)))
            # What this source's own references may name: its label for the
            # dependency, and every label the dependency hoisted into it, each as
            # this source would have bound it.
            view[d.namespace] = bound
            for label, target in self.labels.get(bound, {}).items():
                view.setdefault(d.alias.get(label, label), target)

    def _resolve(self, d: Source, root: Path, via: tuple[str, ...]) -> ResolvedSource:
        key = (d.url, d.ref, d.subdir,
               None if d.path is None else str((root / d.path).resolve()))
        hit = self._memo.get(key)
        if hit is None:
            try:
                hit = _resolve_with(self._resolver, d, root)
            except ResolverError as e:
                raise ResolverError(f"{e} (reached via {_chain(via)})") from e
            self._memo[key] = hit
        return hit

    def projects(self) -> dict:
        """The namespace -> Project view the union engine consumes."""
        return {ns: rs.project for ns, rs in self.resolved.items()}


def resolve_sources(sources, root, *, resolver: Resolver | None = None) -> Resolution:
    """Resolve each declared source and its closure into a :class:`Resolution`
    (SR-0230, SR-0232).

    Every fetch goes through the one resolver interface; no other code path
    reaches a source. ``resolver`` is the work only a host can do, taken as a
    parameter (SR-0226): ``None`` consults the registry, whose default reaches the
    file system and git; a browser passes its own. Composing a source composes
    what it composes: every source a declared source declares, to any depth, is
    bound under the label its declaring source gave it at the pin that source set,
    renamed only by an alias the consumer set on the declared source carrying it.
    A namespace bound to two editions fails fast. Raises :class:`ResolverError`
    for the caller to report, so a source that will not resolve is named in the
    composer's vocabulary."""
    out = Resolution(resolver)

    # 1. Directly declared sources, resolved side by side (throughline-compose
    #    SR-0044). Each look-up of a pinned ref on its origin and each fetch runs
    #    beside the others, and the union is bound in declared order whatever
    #    order they finish in. Two sources sharing a URL and ref share a cache
    #    directory, so only the first of each such pair runs concurrently; the
    #    rest resolve afterwards from the warm cache, because two clones into
    #    one directory at once would corrupt it. The consumer's own declarations
    #    bind first, so a name the consumer chose always wins the label.
    for s, resolved in zip(sources, _resolve_side_by_side(sources, root, resolver)):
        out.bind(s.namespace, resolved, source_location(s), s)

    # 2. Each declared source's closure, depth-first, in declared order.
    for s in sources:
        out.walk(s.namespace, lambda n, _a=s.alias: _a.get(n, n))

    return out


def _cache_key(s):
    """What two sources share when they share a cache directory: the URL and ref."""
    return (s.url, s.ref) if s.url is not None else None


def _resolve_side_by_side(sources, root, resolver: Resolver | None = None):
    """Resolve declared sources concurrently, returning results in declared order.

    The first source for each cache key runs in the pool; a later source with the
    same key waits for the pool and resolves alone afterwards. An error is raised
    for the first failing source in declared order, so the report reads as it did
    when resolution was sequential.
    """
    from concurrent.futures import ThreadPoolExecutor

    results: list = [None] * len(sources)
    seen: set = set()
    first: list[int] = []
    later: list[int] = []
    for i, s in enumerate(sources):
        key = _cache_key(s)
        if key is None or key not in seen:
            seen.add(key)
            first.append(i)
        else:
            later.append(i)

    def one(i: int) -> ResolvedSource:
        return _resolve_with(resolver, sources[i], root)

    if first and _can_thread():
        try:
            with ThreadPoolExecutor(max_workers=min(8, len(first))) as pool:
                futures = {i: pool.submit(one, i) for i in first}
                for i in first:  # declared order, so the first failure is the one reported
                    results[i] = futures[i].result()
        except RuntimeError as e:
            # A platform that has the module but cannot start a thread. Nothing
            # has been bound yet, so resolve every source in order as before.
            if "thread" not in str(e).lower():
                raise
            for i in range(len(sources)):
                results[i] = one(i)
            return results
    else:
        for i in first:
            results[i] = one(i)
    for i in later:
        results[i] = one(i)
    return results


def _can_thread() -> bool:
    """Whether this platform can run a thread beside the main one.

    Python under Pyodide — the browser editor's worker — reports itself as
    emscripten and cannot start a thread, so there the sources resolve one after
    another. The requirement is about running side by side where that is possible,
    not about requiring threads to exist.
    """
    import sys

    return sys.platform != "emscripten"


# ------------------------------------------------- one composition, as a whole


@dataclass
class Composition:
    """A consumer with its declared sources resolved and folded into one union
    (SR-0230): what every union-aware operation works over."""

    consumer: Project
    sources: list[Source]
    resolution: Resolution
    union: Union

    @property
    def project(self) -> Project:
        """The union graph, governed by the consumer's schema."""
        return self.union.project


def compose(consumer: Project, *, resolver: Resolver | None = None) -> Composition | None:
    """Resolve and fold the sources ``consumer`` declares, or return ``None`` when
    it declares none (SR-0230, SR-0232).

    Raises :class:`SourceError` for a malformed declaration, :class:`ResolverError`
    for a source that cannot be reached, and :class:`ComposeError` for a union that
    cannot be built — each in the composer's own vocabulary, for the caller to
    report. ``resolver`` is the parameter of SR-0226; ``None`` uses the registry."""
    sources = parse_sources(consumer)
    if not sources:
        return None
    resolution = resolve_sources(sources, Path(consumer.path), resolver=resolver)
    union = build_union(consumer, resolution.projects(), resolution.labels)
    return Composition(consumer, sources, resolution, union)


# ------------------------------------------------- what check reports (SR-0230)

# The line of core's summary that composition must rescope. Located by its label
# rather than by position, and asserted by a test against core's real output, so a
# change to core's format fails the build loudly instead of degrading a user's report
# quietly. Nothing else in the summary is touched.
_GROUNDING_LABEL = "  Grounding  "


def local_grounding(union: Union, schema, index: Index, local) -> tuple[int, int, int, int]:
    """The grounding figures for the consumer's own items (throughline-compose
    SR-0029).

    The terminus is widened exactly as ``apply_seam`` widens it — "a root, or
    anything borrowed" — so the headline and the findings answer the same question
    and cannot disagree. A local item grounded through a source counts as grounded
    in both, because it is one walk of core's own ``Index.reaches``, not a second
    grounding engine.
    """
    non_roots = [it for it in local if not schema.is_root(it)]
    grounded = sum(
        1 for it in non_roots
        if index.reaches(
            it.uid,
            lambda i: schema.is_root(i) or is_borrowed(union, i.uid),
            schema.ground_link_types,
        )
    )
    # A local delivery root may be served by a borrowed item, so the in-links are
    # read over the whole union even though the roots counted are the consumer's.
    delivery = [it for it in local if it.type in schema.delivery_roots]
    served = sum(
        1 for it in delivery
        if any(lt in schema.ground_link_types for _o, lt in index.in_links(it.uid))
    )
    return grounded, len(non_roots), served, len(delivery)


def composed_check_summary(union: Union, index: Index | None = None) -> list[str]:
    """The graph summary ``check`` prints over the composed union (throughline-compose
    SR-0022, SR-0029).

    The item, status and link lines are byte-identical to what ``tl check`` prints
    over a standalone graph but computed over the union, so a composer sees the size
    of what was actually validated. A trailing ``Local`` line then splits the
    consumer's own items from the ones borrowed through a source (a union item is
    local exactly when its UID is not a mangled, source-owned one).

    The grounding line is the exception, and is rescoped to the consumer's own items.
    Counted over the union it reports a shortfall no reader can close: borrowed items
    ground under the model of the graph that owns them, which a consumer is not
    obliged to restate, so they read as orphans of a model that was never theirs.
    Printed directly above a verdict of zero errors, that figure teaches the reader
    to distrust the verdict. The line says what it counts so its scope is never
    inferred from its size.
    """
    lines = list(check_summary(union.project))
    live = [it for it in union.project.items() if not it.is_deleted]
    local = [it for it in live if union.qualified(it.uid) == it.uid]
    borrowed = len(live) - len(local)

    if index is None:
        index = Index.build(union.project)
    grounded, non_roots, served, delivery = local_grounding(
        union, union.project.schema, index, local
    )
    scoped = (
        f"{_GROUNDING_LABEL}{grounded}/{non_roots} local non-root items trace to a "
        f"root · {served}/{delivery} local delivery roots served"
    )
    for i, line in enumerate(lines):
        if line.startswith(_GROUNDING_LABEL):
            lines[i] = scoped
            break
    else:  # core's format moved; keep the honest figure rather than lose it
        lines.append(scoped)

    breakdown = _by_count(it.type for it in local) if local else "none"
    lines.append(
        f"  Local      {len(local)} of {len(live)} local   {breakdown}"
        f"  ·  {borrowed} borrowed"
    )
    return lines


def describe_binding(res: Resolution, ns: str) -> str:
    """One bound namespace as the check summary names it: label, location, the
    edition's fingerprint, and the path that carried it in."""
    fp = res.resolved[ns].fingerprint.removeprefix("sha256:")[:12]
    line = f"{ns} ({res.locations[ns]}) [{fp}]"
    return f"{line} via {_chain(res.via[ns])}" if res.via[ns] else line


# ------------------------------------------ naming a borrowed item (SR-0230)


def owning_source(uid: str) -> str | None:
    """The namespace a displayed UID was borrowed from, or ``None`` when it is the
    consumer's own. Provenance as data rather than something a reader has to parse
    back out of a qualifier."""
    return uid.split(":", 1)[0] if is_namespace_qualified(uid) else None


def union_uid(union: Union, requested: str) -> str:
    """The union's key for a UID the composer typed. A consumer-local UID or an
    already-mangled UID is present verbatim; a namespace-qualified one
    (``gds:SR-0019``) is matched by its reconstructed display form. Returns
    ``requested`` unchanged when nothing matches, so the caller reports it as
    not-found in the composer's own vocabulary."""
    if union.project.get(requested) is not None:
        return requested
    for it in union.project.items():
        if union.qualified(it.uid) == requested:
            return it.uid
    return requested


def union_view(union: Union) -> GroundingView:
    """The union as a link operation judges it (throughline-compose SR-0049): its
    grounding index, and a target as the composer wrote it found through the same
    resolution ``link`` uses. The union rewrites a consumer's namespace-qualified
    targets to the keys this finds, so the index and the lookup name one edge the
    same way."""
    return GroundingView(Index.build(union.project),
                         lambda target: union.project.get(union_uid(union, target)))


def local_view(view: Project, consumer: Project) -> Project:
    """``view`` narrowed to the consumer's own registers (throughline-compose
    SR-0040). Ownership is read from the consumer's own register set rather than
    inferred from the shape of a displayed name, so narrowing cannot disagree with
    what was loaded."""
    out = Project(path=view.path, config=view.config)
    for prefix, reg in view.registers.items():
        if prefix in consumer.registers:
            out.registers[prefix] = reg
    return out


# The composition block's own schema version, independent of core's
# `dump_schema_version`: the two documents evolve on separate release cycles, and a
# reader that keyed off core's alone could not tell a composition change from none.
COMPOSE_DUMP_SCHEMA_VERSION = 1


def dump_composition(res: Resolution, view: Project, *, local: bool) -> dict:
    """The scope an export answered over (throughline-compose SR-0040): which
    sources were composed at which pinned edition, how many items are the
    consumer's own, and whether the document was narrowed.

    The counts are taken over the whole union even for a narrowed export, because
    what they exist to tell a reader is how much is *missing* — a partial export
    that reported only what it contains would state its restriction and then leave
    its size indistinguishable from a whole one's."""
    per_source = dict.fromkeys(res.resolved, 0)
    local_count = 0
    for it in view.items():
        ns = owning_source(it.uid)
        if ns is None:
            local_count += 1
        else:
            per_source[ns] = per_source.get(ns, 0) + 1
    return {
        "compose_dump_schema_version": COMPOSE_DUMP_SCHEMA_VERSION,
        "scope": "local" if local else "composed",
        "local_item_count": local_count,
        "borrowed_item_count": sum(per_source.values()),
        "sources": [_dump_source(ns, res, per_source[ns])
                    for ns in sorted(res.resolved)],
    }


def _dump_source(ns: str, res: Resolution, item_count: int) -> dict:
    """One composed source as data. The fingerprint is the edition that was
    actually read, which is what a mutable ref cannot be trusted to name on its
    own."""
    s = res.coordinates[ns]
    return {
        "namespace": ns,
        "url": s.url,
        "ref": s.ref,
        "path": s.path,
        "subdir": s.subdir,
        "origin": res.locations[ns],
        "fingerprint": res.resolved[ns].fingerprint,
        "item_count": item_count,
    }


# ------------------------------------------- the document side (SR-0235)

_NS_SPLIT = re.compile(r"^([a-z][a-z0-9_-]*):(.+)$")


class UnionResolver(TargetResolver):
    """Resolve tl:matrix target cells and mirrored clauses over a consumer plus its
    sources (SR-0110, SR-0113, SR-0187).

    ``tl docs`` injects over the *local* consumer project, so counts, tables and
    matrix rows stay byte-identical; the one seam is a namespace-qualified target,
    which resolves against the loaded source for that namespace and so can render
    the borrowed clause's own reference number. An unqualified target falls through
    to the consumer project, so behaviour over local links is identical to the
    default."""

    def __init__(self, consumer, sources: dict,
                 labels: dict[str, dict[str, str]] | None = None) -> None:
        super().__init__(consumer)
        self._sources = sources  # namespace -> loaded source Project
        # namespace -> that source's own label -> union namespace, so a mirrored
        # clause's outgoing cross-source links read under the names the citing
        # document uses, not the labels the source's author chose.
        self._labels = labels or {}

    def _delegate(self, uid: str) -> "TargetResolver | None":
        """A resolver over the source project owning ``uid``, or ``None`` when
        ``uid`` is not a namespace-qualified reference to a declared source."""
        if not is_namespace_qualified(uid):
            return None
        m = _NS_SPLIT.match(uid)
        src = self._sources.get(m.group(1))
        return TargetResolver(src) if src is not None else None

    def present(self, uid: str) -> bool:
        d = self._delegate(uid)
        return d.present(_local(uid)) if d else super().present(uid)

    def attr(self, uid: str, name: str):
        d = self._delegate(uid)
        return d.attr(_local(uid), name) if d else super().attr(uid, name)

    def link_display(self, uid: str) -> str:
        """Enrich a borrowed clause's link display with its own reference number
        (SR-0113): ``asvs:SR-0172`` reads ``asvs:SR-0172 (V7.1.1)`` when the source
        clause carries a ``source_ref``. A local target is the bare UID as before."""
        if not is_namespace_qualified(uid):
            return super().link_display(uid)
        ref = self.attr(uid, "source_ref")
        return f"{uid} ({ref})" if ref else uid

    def mirror_block(self, uid: str) -> str | None:
        """The borrowed clause's own full block, stated under the identity the citing
        document uses for it (SR-0235). Returns ``None`` for a local target or one
        whose namespace names no declared source, so the caller can report which
        reference it could not mirror rather than dropping it.

        The block is produced by the item renderer over the *source* project, with
        a resolver that qualifies every UID it renders: the heading through the
        identity seam (SR-0187) and the clause's outgoing links through the link seam
        (SR-0113). Without that, a mirrored clause would be published under the
        source's own local UID and collide with an unrelated consumer item of the
        same number."""
        src = self._source_for(uid)
        if src is None:
            return None
        local = _local(uid)
        if src.get(local) is None:
            return None
        ns = _NS_SPLIT.match(uid).group(1)
        return render_item(src, local,
                           _MirrorResolver(src, ns, self._labels.get(ns, {})))

    def _source_for(self, uid: str):
        """The loaded source project owning a namespace-qualified ``uid``, or None."""
        if not is_namespace_qualified(uid):
            return None
        return self._sources.get(_NS_SPLIT.match(uid).group(1))


class _MirrorResolver(TargetResolver):
    """Renders one source's items under the consumer's namespace for that source.

    A source graph knows nothing of the namespace a consumer binds it to, so every
    UID it would render — the clause's own, and each of its outgoing link targets —
    is qualified here. Reference numbers take the same ``UID (ref)`` form the
    consumer's citations already use, so the heading a reader arrives at matches
    the citation that sent them there."""

    def __init__(self, source, namespace: str,
                 labels: dict[str, str] | None = None) -> None:
        super().__init__(source)
        self._ns = namespace
        self._labels = labels or {}

    def _qualified(self, uid: str) -> str:
        """``uid`` under this source's namespace, with its reference number where the
        clause carries one. A target already qualified is a reference the source makes
        into a further namespace of its own: its label is mapped to the union
        namespace the consumer bound it under and otherwise left exactly as the
        source wrote it — re-qualifying it would claim it for the wrong graph."""
        if is_namespace_qualified(uid):
            m = _NS_SPLIT.match(uid)
            bound = self._labels.get(m.group(1))
            return f"{bound}:{m.group(2)}" if bound else uid
        ref = self.attr(uid, "source_ref")
        return f"{self._ns}:{uid} ({ref})" if ref else f"{self._ns}:{uid}"

    def display(self, uid: str) -> str:
        return self._qualified(uid)

    def link_display(self, uid: str) -> str:
        return self._qualified(uid)


def _local(uid: str) -> str:
    """The source-local UID of a namespace-qualified reference (``asvs:SR-0227``
    → ``SR-0227``)."""
    return _NS_SPLIT.match(uid).group(2)


_PLACEHOLDER = "_(the items this filter selects reference no external clause)_"


def render_sourced(project, expr: str, resolver) -> str:
    """A full-clause mirror (SR-0235): the distinct external clauses that the items
    matching ``expr`` reference by a namespace-qualified link target, each rendered
    in full, in target order, separated by a blank line.

    Each clause is stated under the identity the citing document uses for it — the
    namespace-qualified target and, where the clause carries one, its reference
    number — never under the source graph's own local UID. Where the matching items
    reference no external clause there is nothing to mirror and a placeholder is
    rendered; where a referenced clause cannot be rendered from its declared source,
    injection fails naming the marker rather than quietly dropping it. A malformed
    filter fails injection (via ``matching``)."""
    targets = _external_targets(project, expr)
    if not targets:
        return _PLACEHOLDER

    mirror = getattr(resolver, "mirror_block", None)
    if mirror is None:
        # The document cites external clauses but the resolver holds no sources —
        # the project declares none, or the composed union never reached injection.
        # Saying so beats mirroring nothing and leaving the reader a document that
        # looks complete.
        raise InjectError(
            f"tl:sourced cannot mirror {', '.join(targets)} — no composed sources "
            "were available to this run")

    blocks = []
    for t in targets:
        b = mirror(t)
        if b is None:
            raise InjectError(
                f"tl:sourced cannot mirror '{t}' — its namespace names no declared "
                "source, or that source holds no such clause. Declare the namespace "
                "in [[sources]], or correct the reference.")
        blocks.append(b)
    return "\n\n".join(blocks)


def _external_targets(project, expr: str) -> list[str]:
    """The distinct namespace-qualified link targets of the items matching ``expr``,
    in target order — what the selected items borrow, deduplicated."""
    seen: set[str] = set()
    for it in matching(project, expr):
        for link in it.links:
            if is_namespace_qualified(link.target):
                seen.add(link.target)
    return sorted(seen)


# A mirrored clause belongs to its source, so the directive does not publish the
# *local* items its filter selects for the coverage rule (SR-0096) — those items are
# selected only to discover what they borrow. Declaring that on the registry entry is
# the only place it is said (SR-0186). Registered here, at import, because the Tool
# now holds the sources the directive needs (NG-0007, SR-0235).
register_directive(
    "sourced", render_sourced, publishes=False,
    selects=lambda project, arg: [it.uid for it in matching(project, arg)])


# --------------------------------------------- the agent brief (SR-0230)

def bound_line(res: Resolution, ns: str) -> str:
    """One human-readable bullet describing a bound namespace for the live listing:
    its pin, and the path that carried it in or the alias set on it."""
    s = res.coordinates[ns]
    where = f"`{s.url}` @ `{s.ref}`" if s.is_remote else f"path `{s.path}`"
    if s.subdir:
        where += f" (subdir `{s.subdir}`)"
    via = res.via[ns]
    if via:
        carried = " · via " + " › ".join(f"`{v}`" for v in via)
    else:
        carried = " · declared by you"
        if s.alias:
            parts = ", ".join(f"`{k}` → `{v}`" for k, v in sorted(s.alias.items()))
            carried += f" · alias {parts}"
    return f"- **`{ns}`** — {where}{carried}"


def bound_section(res: Resolution) -> str:
    """The live 'namespaces bound in this union' section — the composition analogue
    of the brief's live graph snapshot: every namespace the union binds, the
    sources this project declares and every transitive source those carry, each
    with its pin and the path that carried it, so the brief describes the
    composition the agent is really working in."""
    lines = ["## Namespaces bound in this union\n"]
    lines.extend(bound_line(res, ns) for ns in res.resolved)
    if res.notices:
        lines.append("")
        lines.extend(f"- _{note}_" for note in res.notices)
    return "\n".join(lines)


# The composition half of the agent brief. `tl context` (alias `agentinfo`) emits the
# ordinary brief first — every rule above it holds, because a composed project is a
# normal throughline graph — and then appends this section, which describes the part
# composition adds.
COMPOSITION_BRIEF = """\
---

# Composition: working a project that declares sources

Everything above holds **unchanged**: a composed project is a normal throughline
graph, validated by the very same rules, and `tl-compose` is simply a second name
for `tl`. What follows is the part composition adds.

## What composition does

A composed project stays a normal throughline graph, but it may **reference clauses
that live in *other* throughline graphs** — a published standard (OWASP ASVS, GOV.UK,
WCAG), a sibling requirement set, a content-style axis — without copying them in.
Those external graphs are **sources**. A clause in a source is referenced from your
graph as `<namespace>:<UID>` (for example `asvs:V2.1.1`), where the *namespace* is a
label **you** choose. `tl check` merges the consumer and its sources into one
**union graph**, runs the unchanged validator over it, and reports every finding
back in `<namespace>:<UID>` vocabulary — so a link from a local requirement up into a
borrowed clause is validated, never left dangling.

## Declaring sources

Sources are declared as an array of `[[sources]]` tables in `throughline.toml`. Each
binds a namespace to one external graph:

```toml
[[sources]]
namespace = "asvs"                 # the label you reference it by
url = "https://github.com/rhodium-org/throughline-asvs"
ref = "v5.0.0"                     # REQUIRED for a url — pins the edition
```

- **`url` + `ref`** — a git origin pinned to an edition (normally a tag). The durable,
  shareable form; fetched into a per-user cache. A `url` **must** carry a `ref` — an
  unpinned dependency is rejected so a source can never silently track a moving branch.
- **`path`** — a local directory instead of a `url`, for developing a source and its
  consumer side by side (`url`/`ref` and `path` are mutually exclusive; a `path` takes
  no `ref`).
- **`subdir`** — optional, on either form: the graph lives in this directory relative
  to the repository (or `path`) root.

`[[sources]]` is config, so it is the one part of a composed project you edit by
hand — there is no CLI subcommand that writes it. Everything about the *graph
itself* stays CLI-only; see **What you may write in a consuming project**, below.

## Transitive sources — composing a source composes what it composes

Every namespace a declared source declares — and every one *those* declare, to any
depth — is bound into your union under the label its declaring source gave it, at
the pin that source set. You declare only the sources you cite directly and choose
their labels; the rest arrive on their own, each edition inherited and never
restated. The check summary and the listing below name every bound namespace with
the path that carried it in (`regulation … via house › platform`), so the toml says
what you chose and the tool says what that composes.

Your one lever over a transitive label is `alias`, on the declared source that
carries it:

```toml
[[sources]]
namespace = "house"
url = "..."
ref = "v2026-07"
alias = { asvs = "asvs-v4" }       # house's `asvs`, and any `asvs` beneath it, binds as `asvs-v4`
```

An alias applies throughout that source's subtree. A source's own references always
resolve through its own declarations (renamed by your alias), never against a label
you happen to reuse — so a source that calls its dependency `platform` cannot be
captured by an unrelated `platform` you declared. Your own items may cite any bound
namespace, transitive or direct, by its bound label; a reference to a namespace
nothing binds fails, naming the item that carries it and listing what is bound.

Two labels reaching the union at **one edition** bind once, under the first label
bound, and the summary says which was folded into which. One label reaching the
union at **two different editions** — declared at one ref and carried in at another,
or two sources pinning the same standard differently — is refused: `tl` names both
editions and the path each came by, and states the fix (pin it yourself to the one
edition you intend, or set an `alias` on the declared source carrying one of them so
both compose side by side). The old `reexport` key is refused in your own toml and
ignored, with a note, inside a source's.

@@UNION_COMMANDS@@

## The source cache — a moved ref **is** picked up

A `url` + `ref` source is fetched into a per-user cache keyed by that exact
`(url, ref)` pair, then reused rather than cloned again. Reuse is checked, never
assumed, because a pin is not the same thing as an immutable edition:

> **A commit id** names one commit for all time, so its checkout is reused with no
> network access at all.
>
> **A tag or a branch** is a name the origin can move, so `tl` asks the origin what
> it points at now and refetches only when it has moved. An unmoved ref costs one
> ref advertisement and no download.

So moving a tag *is* picked up, on the next run, and you never need to clear the
cache to see changed content behind a ref you have already used. A ref the origin
resolves ambiguously is an error rather than a guess.

`TL_OFFLINE=1` (or the older `TL_COMPOSE_OFFLINE=1`) composes from the cache without
contacting any origin, for a genuinely disconnected machine; a source missing from
the cache is then an error rather than a fetch. Without it, an origin that cannot be
reached fails the run — it never falls back silently to whatever happens to be
cached, because a check that cannot see the content it is gating is not a gate. A
project that declares no sources never touches the network at all, and neither does
any command that does not read the union. This project's cache lives at:

```
@@CACHE_ROOT@@
```

## What you may write in a consuming project

A source is **read-only**. Composition gives you a wider *view*, never a wider
*authority*, so in a consuming project:

- **You write only to your own registers.** Every item you create, link, restatus or
  ratify is yours. A borrowed clause is never edited, never restatused, and **never
  ratified by you** — its own graph owns its accountability record, and a
  `<namespace>:<UID>` argument to a writing command is a mistake, not a shortcut.
- **You may point *at* a source freely.** `--ground base:RISK-0001`, `link SR-0007
  base:RISK-0001 --type mitigates` — the link is stored on *your* item, namespace-
  qualified exactly as typed.
- **`[[sources]]` is the one thing you hand-edit,** because it is config, not graph.
  Items, links, statuses and UIDs stay CLI-only exactly as the brief says: use
  `tl new`/`link`/`ratify`, never hand-edit a `<UID>.yml` or a `.register.yml`.

## The boundary (NG-0008)

Composition reaches only what a project declares: no network unless a `url` source
is declared, and then only its origin at the pinned ref; cache first, and never a
fetch in cache-only mode; nothing resolved for a command that does not read the
union; no moving head; and never a write back to a source or to an external
authority. Storing a link *inside* a source, an issue tracker, or a wiki is a
connector's job, not composition's.
"""


def composition_brief(union_commands: str) -> str:
    """The composition section, with its derived parts filled in.

    Substitution is by literal replacement rather than ``str.format`` because the
    prose contains TOML examples with braces in them; a formatting pass over
    hand-written documentation is a trap that goes off the next time someone adds an
    inline table to an example."""
    return (COMPOSITION_BRIEF
            .replace("@@UNION_COMMANDS@@", union_commands)
            .replace("@@CACHE_ROOT@@", str(cache_root())))
