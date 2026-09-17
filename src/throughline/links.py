# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Adding, restamping, retyping and removing a link (SR-0143, SR-0211, SR-0212).

These are the operations behind `tl link` and `tl unlink`, offered here so a tool
that grounds over a wider graph than the one it writes, such as tl-compose over a
union, performs them through the Tool rather than through a copy that falls behind.
Such a tool passes a :class:`GroundingView`; everything else is the operation's own.
"""
from __future__ import annotations

from typing import Callable, NamedTuple

from .fingerprint import fingerprint
from .graph import Index
from .grounding import ungrounded_by
from .model import Item, Link
from .storage import write_item


class LinkError(ValueError):
    """A link operation refused, saying why. Nothing was written."""


class GroundingView(NamedTuple):
    """The graph a link operation judges against (SR-0212): a grounding index, and
    how a target written on an item is found in it. Left out, it is the project's
    own graph."""
    index: Index
    find: Callable[[str], Item | None]


def own_view(project) -> GroundingView:
    return GroundingView(Index.build(project), project.get)


def _source(project, src_uid: str) -> Item:
    src = project.get(src_uid)
    if src is None:
        raise LinkError(f"source {src_uid} does not exist")
    return src


def _target(view: GroundingView, target: str) -> Item:
    dst = view.find(target)
    if dst is None:
        raise LinkError(f"target {target} does not exist")
    return dst


def _refuse_ungrounding(project, view: GroundingView, src_uid: str, change: str, *,
                        remove, add=()) -> None:
    """Refuse ``change`` when it would leave the graph ungrounded (SR-0211)."""
    after = view.index.with_edges(src_uid, remove=remove, add=add)
    orphaned, unserved = ungrounded_by(project.schema, list(project.items()),
                                       view.index, after)
    if not orphaned and not unserved:
        return
    harm = []
    if orphaned:
        harm.append(f"leave {', '.join(orphaned)} reaching no root")
    if unserved:
        harm.append(f"leave {', '.join(unserved)} served by nothing")
    raise LinkError(f"{change} would {' and '.join(harm)} — nothing was changed; "
                    "link the replacement first, then remove this one")


def add_link(project, src_uid: str, target: str, link_type: str, *,
             stamp: bool = False, view: GroundingView | None = None) -> str:
    """Add ``src --link_type--> target``. Where that edge already exists, refresh
    its stamp in place when ``stamp`` is asked for, and refuse otherwise, since a
    parallel duplicate would hide a stale stamp behind a fresh one (SR-0034).
    Returns ``"linked"`` or ``"restamped"``."""
    view = view or own_view(project)
    src = _source(project, src_uid)
    dst = _target(view, target)
    value = fingerprint(dst, project.schema) if stamp else None
    same = [ln for ln in src.links if ln.target == target and ln.type == link_type]
    if same:
        if not stamp:
            raise LinkError(f"{src_uid} --{link_type}--> {target} already exists "
                            "(add --stamp to refresh its stamp, or --retype to "
                            "change its type)")
        for ln in same:
            ln.stamp = value
        write_item(src, project.register_of(src.uid))
        return "restamped"
    src.links.append(Link(target=target, type=link_type, stamp=value))
    write_item(src, project.register_of(src.uid))
    return "linked"


def retype_link(project, src_uid: str, target: str, link_type: str, *,
                stamp: bool = False, view: GroundingView | None = None) -> str:
    """Change the type of the edge joining ``src`` to ``target`` in place
    (SR-0143), refused where the new type would leave the graph ungrounded
    (SR-0211). Returns the type it had."""
    view = view or own_view(project)
    src = _source(project, src_uid)
    dst = _target(view, target)
    existing = [ln for ln in src.links if ln.target == target]
    if not existing:
        raise LinkError(f"no existing link {src_uid} -> {target} to retype "
                        "(drop --retype to add a new one)")
    types = {ln.type for ln in existing}
    if len(types) > 1:
        raise LinkError(f"multiple link types {src_uid} -> {target} "
                        f"({', '.join(sorted(types))}); remove the unwanted one "
                        "with `tl unlink` first")
    old = existing[0].type
    _refuse_ungrounding(project, view, src_uid,
                        f"retyping {src_uid} --{old}--> {target} to {link_type}",
                        remove={(dst.uid, old)}, add=[(dst.uid, link_type)])
    value = fingerprint(dst, project.schema) if stamp else None
    for ln in existing:
        ln.type = link_type
        if stamp:
            ln.stamp = value
    write_item(src, project.register_of(src.uid))
    return old


def remove_link(project, src_uid: str, target: str, link_type: str | None = None, *,
                view: GroundingView | None = None) -> list[str]:
    """Remove the edge joining ``src`` to ``target`` (SR-0143), naming its type when
    several join them, refused where the removal would leave the graph ungrounded
    (SR-0211). Returns the types removed."""
    view = view or own_view(project)
    src = _source(project, src_uid)

    def matches(ln) -> bool:
        return ln.target == target and (link_type is None or ln.type == link_type)

    matched = [ln for ln in src.links if matches(ln)]
    if not matched:
        what = f" of type '{link_type}'" if link_type else ""
        raise LinkError(f"no link {src_uid} -> {target}{what} to remove")
    types = sorted({ln.type for ln in matched})
    if link_type is None and len(types) > 1:
        raise LinkError(f"multiple link types {src_uid} -> {target} "
                        f"({', '.join(types)}); pass --type to choose which to remove")
    dst = view.find(target)
    # A target the view cannot find grounds nothing, so removing a link to it can
    # leave nothing ungrounded.
    if dst is not None:
        _refuse_ungrounding(project, view, src_uid,
                            f"removing {src_uid} --{types[0]}--> {target}",
                            remove={(dst.uid, k) for k in types})
    src.links = [ln for ln in src.links if not matches(ln)]
    write_item(src, project.register_of(src.uid))
    return types
