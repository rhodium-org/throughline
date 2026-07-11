# Copyright (c) 2026 Time Back Solutions Limited
# SPDX-License-Identifier: Apache-2.0
"""Link-graph index — built once, queried by every service (arch doc 07 §5).

Forward = links stored on an item; backward = who points at it. Used for
suspect detection, impact/trace (SR-0035), coverage, and the grounding layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field


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

    def impact(self, uid: str, link_types: set[str] | None = None) -> list[str]:
        """Transitive set reachable via *incoming* links — what depends on this
        (SR-0035 / blast radius)."""
        hit: list[str] = []
        seen = {uid}
        stack = [uid]
        while stack:
            for src, _k in self.in_links(stack.pop(), link_types):
                if src not in seen:
                    seen.add(src)
                    hit.append(src)
                    stack.append(src)
        return hit

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
