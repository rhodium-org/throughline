# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Ways of showing an item and its neighbourhood, as text a caller places where it
likes (SR-0224).

A trace, a subgraph, and the item a ratifier is about to sign. Each front end draws
the same lines because each asks the Tool for them (SR-0190, SR-0195), and each
writes through an ``emit`` callable, so a terminal, a document and a browser differ
only in where the lines go.
"""
from __future__ import annotations

import json

from pathlib import Path

from .graph import Index
from .grounding import reaches_root
from .grounding import (
    CLARIFICATION_ATTRS,
    CLARIFIED_AMBIGUITY_ATTR,
    CLARIFIED_BY_ATTR,
    CLARIFIED_REASON_ATTR,
)

def _by_count(pairs) -> str:
    """'a 3 · b 1' from an iterable of names, most-frequent first."""
    from collections import Counter
    c = Counter(pairs)
    return " · ".join(f"{name} {n}" for name, n in c.most_common())


def render_trace(project, start: str, *, direction: str = "out", max_depth: int = 0,
                 uid_display=None, expand=None, emit=print) -> None:
    """Print the link tree rooted at ``start`` as an indented ASCII tree.

    The single walk both ``tl trace`` and ``tl-compose trace`` share, so the tree
    glyphs cannot drift between the two front ends. Two seams let a composing
    caller specialise the view without touching the walk:

    - ``uid_display(uid) -> str`` renders the label shown for a node's UID
      (default: the UID itself); tl-compose passes the namespace-qualified form so
      a borrowed clause reads ``asvs:SR-0272`` rather than its mangled union UID.
    - ``expand(uid) -> bool`` decides whether to recurse into a node's own links
      (default: always). tl-compose stops at the source boundary by expanding only
      consumer-local items, so a borrowed clause is shown but its source-internal
      graph is not dragged in.

    ``max_depth`` of 0 means unlimited. ``emit`` is the sink (default ``print``).
    """
    show = uid_display or (lambda u: u)
    recurse = expand or (lambda u: True)
    idx = Index.build(project)
    seen: set[str] = set()

    def walk(uid: str, depth: int, prefix: str, connector: str, ltype: str | None) -> None:
        line = f"{prefix}{connector}"
        edge = f"({ltype}) " if ltype else ""
        disp = show(uid)
        if uid in seen or (max_depth and depth > max_depth):
            marker = " (cycle)" if uid in seen else ""
            emit(f"{line}{edge}{disp}{marker}")
            return
        seen.add(uid)
        item = project.get(uid)
        if item is None:
            # A link target that is not a local item: a dangling reference, or a
            # namespace-qualified reference to another source (resolved only under
            # tl-compose). Show it as a leaf rather than crashing the walk.
            emit(f"{line}{edge}{disp} (unresolved)")
            return
        label = f"{disp}  [{item.type}/{item.status}] {item.title}".rstrip()
        emit(f"{line}{edge}{label}")
        # Continuation guide for this node's subtree: a vertical bar while more
        # siblings follow ("├─"), blank once this was the last child ("└─") or root.
        child_prefix = prefix + ("│ " if connector == "├─" else "  " if connector else "")
        if not recurse(uid):
            return
        edges = (idx.in_links(uid) if direction == "in" else idx.out_links(uid))
        for i, (other, lt) in enumerate(edges):
            last = i == len(edges) - 1
            walk(other, depth + 1, child_prefix, "└─" if last else "├─", lt)

    walk(start, 0, "", "", None)


def render_subgraph(project, view, *, uid_display=None, emit=print) -> None:
    """Render a Neighbourhood as text (SR-0189).

    The one rendering both ``tl subgraph`` and ``tl context <UID>`` use, so the
    two cannot drift (SR-0190). ``uid_display`` is the same seam ``render_trace``
    offers: tl-compose passes the namespace-qualified form so a borrowed clause
    reads ``asvs:SR-0272`` rather than its mangled union UID.
    """
    show = uid_display or (lambda u: u)

    def label(uid: str) -> str:
        item = project.get(uid)
        if item is None:
            return f"{show(uid)}  (unresolved)"
        return f"{show(uid)}  [{item.type}/{item.status}] {item.title}".rstrip()

    emit(label(view.start))
    for heading, group in (("rests on", view.upstream),
                           ("depended on by", view.downstream)):
        emit("")
        if group:
            emit(f"{heading} ({len(group)}):")
            for uid in group:
                emit(f"  {label(uid)}")
        else:
            emit(f"{heading}: none")
    emit("")
    if view.edges:
        emit(f"links within this set ({len(view.edges)}):")
        width = max(len(show(s)) for s, _lt, _t in view.edges)
        for src, ltype, tgt in view.edges:
            emit(f"  {show(src).ljust(width)}  -{ltype}->  {show(tgt)}")
    else:
        emit("links within this set: none")


def subgraph_json(project, view, uid_display=None) -> dict:
    show = uid_display or (lambda u: u)

    def node(uid: str) -> dict:
        item = project.get(uid)
        if item is None:
            return {"uid": show(uid), "resolved": False}
        return {"uid": show(uid), "resolved": True, "type": item.type,
                "status": item.status, "title": item.title}

    return {
        "start": show(view.start),
        "upstream": [show(u) for u in view.upstream],
        "downstream": [show(u) for u in view.downstream],
        "nodes": [node(u) for u in view.nodes],
        "edges": [{"source": show(s), "type": lt, "target": show(t)}
                  for s, lt, t in view.edges],
    }


def render_for_ratification(project, item, *, emit) -> None:
    """Put the item in front of the person about to sign it (SR-0195, UR-0029).

    Not :func:`throughline.inject.render_item`: that renders Markdown for a
    published document, and states every attribute alike. A ratifier is being asked
    to judge, so this separates the normative attributes — the ones whose change
    breaks the signature — from the rest, and names each grounding target so the
    'why' reads as a sentence instead of a bare UID the reader has to go and look
    up. Emitted on stderr like every other piece of guidance (SR-0120), leaving
    stdout to the one line that says what was ratified.
    """
    schema = project.schema
    emit("")
    emit(f"{item.uid}  [{item.type}/{item.status}] {item.title}".rstrip())
    for label, body in (("", item.text), ("rationale", item.rationale)):
        if not body:
            continue
        emit("")
        if label:
            emit(f"{label}:")
        for line in body.splitlines():
            emit(f"  {line}" if line else "")
    grounding = [ln for ln in (ground_line(project, link, schema)
                               for link in item.links) if ln]
    emit("")
    if grounding:
        emit("grounded by:")
        for line in grounding:
            emit(f"  {line}")
    else:
        # A root needs no grounding and says so; anything else could not have got
        # this far, since ratification_obstacle refuses an ungrounded item.
        emit("grounded by: nothing — this is a root and justifies itself")
    normative = [n for n in schema.normative_attrs(item.type) if n in item.attrs]
    if normative:
        emit("")
        emit("normative attributes (a change here breaks this signature): "
             + " · ".join(f"{n}={item.attrs[n]}" for n in normative))
    # A removed ambiguity flag goes in front of the signer with the item (SR-0214).
    # The ambiguity was about these words, and a flag removed by the item's own
    # author would otherwise never reach the person who signs.
    if any(name in item.attrs for name in CLARIFICATION_ATTRS):
        emit("")
        emit("clarified — this item was flagged ambiguous and the flag was removed:")
        for label, name in (("removed by", CLARIFIED_BY_ATTR),
                            ("reason", CLARIFIED_REASON_ATTR),
                            ("check had reported", CLARIFIED_AMBIGUITY_ATTR)):
            value = item.attrs.get(name)
            lines = str(value).splitlines() if value is not None else ["(not recorded)"]
            emit(f"  {label}: {lines[0] if lines else ''}")
            for line in lines[1:]:
                emit(f"    {line}")
    emit("")


def ground_line(project, link, schema) -> str | None:
    """One grounding link rendered with its target's title, or None for a link that
    confers no grounding — those are context, not justification, and listing them
    here would present a 'relates' neighbour as a reason the item exists."""
    if link.type not in schema.ground_link_types:
        return None
    target = project.get(link.target)
    if target is None:
        return f"{link.type} {link.target}  (unresolved)"
    return f"{link.type} {link.target}  [{target.type}/{target.status}] {target.title}".rstrip()


def check_summary(project) -> list[str]:
    """Human-readable picture of what check validated (SR-0078): what is in the
    graph, how it is linked, and whether every requirement traces to a root."""
    schema = project.schema
    idx = Index.build(project)
    live = [it for it in project.items() if not it.is_deleted]

    links = [l.type for it in live for l in it.links]
    non_roots = [it for it in live if not schema.is_root(it)]
    grounded = sum(1 for it in non_roots if reaches_root(idx, schema, it.uid))
    delivery = [it for it in live if it.type in schema.delivery_roots]
    served = sum(
        1 for it in delivery
        if any(lt in schema.ground_link_types for _o, lt in idx.in_links(it.uid))
    )

    name = schema.name or Path(project.path).name
    lines = [
        "",
        f"tl check · {name}",
        f"  Items      {len(live)} live   {_by_count(it.type for it in live)}",
        f"  Status     {_by_count(it.status for it in live)}",
        f"  Links      {len(links)}        {_by_count(links)}",
        f"  Grounding  {grounded}/{len(non_roots)} non-root items trace to a "
        f"root · {served}/{len(delivery)} delivery roots served",
    ]
    return lines

