# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Link-graph index — built once, queried by every service (arch doc 07 §5).

Forward = links stored on an item; backward = who points at it. Used for
suspect detection, impact/trace (SR-0035), coverage, and the grounding layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Neighbourhood:
    """The induced subgraph around one item (SR-0189).

    ``upstream`` and ``downstream`` are the transitive sets reachable by outgoing
    and incoming links respectively; ``nodes`` is the start plus both, deduplicated
    (a cycle can put one item in each). ``edges`` is every link whose *source and
    target both fall inside* ``nodes`` — the part neither ``trace`` nor ``blast``
    reports, and the reason this is a distinct view rather than a flag on either.
    """
    start: str
    upstream: tuple[str, ...] = ()
    downstream: tuple[str, ...] = ()
    nodes: tuple[str, ...] = ()
    edges: tuple[tuple[str, str, str], ...] = ()   # (source, link type, target)


@dataclass
class Index:
    items: dict = field(default_factory=dict)               # uid -> Item
    forward: dict = field(default_factory=dict)             # uid -> [(target, type)]
    backward: dict = field(default_factory=dict)            # uid -> [(source, type)]

    @classmethod
    def build(cls, project) -> "Index":
        idx = cls()
        for item in project.items():
            idx.items[item.uid] = item
            idx.forward.setdefault(item.uid, [])
            for link in item.links:
                idx.forward[item.uid].append((link.target, link.type))
                idx.backward.setdefault(link.target, []).append((item.uid, link.type))
        return idx

    def out_links(self, uid: str, types: set[str] | None = None) -> list[tuple[str, str]]:
        return [(t, k) for t, k in self.forward.get(uid, [])
                if types is None or k in types]

    def in_links(self, uid: str, types: set[str] | None = None) -> list[tuple[str, str]]:
        return [(s, k) for s, k in self.backward.get(uid, [])
                if types is None or k in types]

    def link_shape(self) -> dict[tuple[str, str, str | None], int]:
        """Every (source type, link type, target type) triple in the graph, with
        counts — the graph's actual link shape (SR-0085). Target type is ``None``
        when the target is not a known item (external or dangling), so the report
        is total. This is the reality a maintainer authors ``[link_rules]`` from,
        instead of reconstructing it by hand."""
        shape: dict[tuple[str, str, str | None], int] = {}
        for uid, edges in self.forward.items():
            src = self.items.get(uid)
            if src is None:
                continue
            for target, ltype in edges:
                tgt = self.items.get(target)
                key = (src.type, ltype, tgt.type if tgt is not None else None)
                shape[key] = shape.get(key, 0) + 1
        return shape

    def reaches(self, uid: str, target_pred, link_types: set[str]) -> bool:
        """True if a path over ``link_types`` from uid hits an item satisfying
        target_pred. The start item is tested too."""
        seen: set[str] = set()
        stack = [uid]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            it = self.items.get(cur)
            if it is None:
                continue
            if target_pred(it):
                return True
            stack.extend(t for t, _k in self.out_links(cur, link_types))
        return False

    def closure(self, uid: str, direction: str, link_types: set[str] | None = None,
                max_depth: int = 0, expand=None) -> list[str]:
        """Transitive set reachable from ``uid`` over one direction, in
        breadth-first discovery order and excluding ``uid`` itself.

        ``direction`` is ``"out"`` (what this rests on) or ``"in"`` (what rests on
        this). ``max_depth`` of 0 means unlimited. The single walk behind both
        ``impact`` and ``subgraph``, so a blast radius and a neighbourhood can
        never disagree about what points at an item (SR-0189).

        ``expand`` is the boundary seam ``render_trace`` also offers: a node it
        rejects is still reported, but is not walked through. tl-compose passes
        one so a neighbourhood touching a borrowed clause does not swallow the
        whole standard behind it. ``uid`` itself is always walked — a caller who
        named it is standing on it deliberately."""
        step = self.out_links if direction == "out" else self.in_links
        hit: list[str] = []
        seen = {uid}
        frontier = [uid]
        depth = 0
        while frontier and (not max_depth or depth < max_depth):
            nxt: list[str] = []
            for cur in frontier:
                if expand is not None and cur != uid and not expand(cur):
                    continue
                for other, _k in step(cur, link_types):
                    if other not in seen:
                        seen.add(other)
                        hit.append(other)
                        nxt.append(other)
            frontier = nxt
            depth += 1
        return hit

    def impact(self, uid: str, link_types: set[str] | None = None) -> list[str]:
        """Transitive set reachable via *incoming* links — what depends on this
        (SR-0035 / blast radius)."""
        return self.closure(uid, "in", link_types)

    def subgraph(self, uid: str, link_types: set[str] | None = None,
                 max_depth: int = 0, expand=None) -> Neighbourhood:
        """The induced subgraph around ``uid`` (SR-0189) — both directed closures
        plus every link joining two members of the resulting node set.

        A target that is not a local item (dangling, or namespace-qualified and
        resolvable only under tl-compose) stays in the node set as a leaf; it has
        no outgoing links here, so the walk stops at it rather than failing.

        ``expand`` bounds only what is *walked through*; a link joining two nodes
        already in the set is still reported, whichever of them it starts at, or
        the drawing would contradict the set it claims to induce."""
        upstream = self.closure(uid, "out", link_types, max_depth, expand)
        downstream = self.closure(uid, "in", link_types, max_depth, expand)
        nodes = [uid, *upstream]
        within = set(nodes)
        for d in downstream:                 # a cycle can put one item in both
            if d not in within:
                within.add(d)
                nodes.append(d)
        edges = sorted({
            (src, ltype, tgt)
            for src in nodes
            for tgt, ltype in self.out_links(src, link_types)
            if tgt in within
        })
        return Neighbourhood(start=uid, upstream=tuple(upstream),
                             downstream=tuple(downstream), nodes=tuple(nodes),
                             edges=tuple(edges))

    def refines_cycle(self, cycle_link_types: set[str]) -> list[str] | None:
        WHITE, GREY, BLACK = 0, 1, 2
        colour = {u: WHITE for u in self.items}
        path: list[str] = []

        def visit(n: str):
            colour[n] = GREY
            path.append(n)
            for t, _k in self.out_links(n, cycle_link_types):
                if t not in colour:
                    continue
                if colour[t] == GREY:
                    return path[path.index(t):] + [t]
                if colour[t] == WHITE:
                    r = visit(t)
                    if r:
                        return r
            colour[n] = BLACK
            path.pop()
            return None

        for node in self.items:
            if colour[node] == WHITE:
                r = visit(node)
                if r:
                    return r
        return None
