# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""What awaits a signature, and why each item can or cannot take one (SR-0229).

The model a review interface draws. It lived in the cockpit, so a browser wanting
it imported a package built for a terminal, and the judgement "may this be signed?"
existed twice — the drift one precondition function (SR-0195) exists to prevent,
one layer out. The judgement is the Tool's; what an interface owns is the drawing.

Staleness is read from the stamp the item carries, not from history, so this model
is available where there is no subprocess to ask git in (SR-0227).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .fingerprint import fingerprint
from .graph import Index
from .grounding import (
    is_flagged_ambiguous,
    ratification_obstacle,
    reaches_root,
    transition_refusal,
)
from .identity import RATIFIED_BY_ATTR

#: The states a reviewer sorts by, most actionable first. `proposed` and `ready`
#: can be signed now; `ambiguous`, `ungrounded` and `blocked` must be fixed first;
#: `stale` was signed and then rewritten; the rest are settled or dead.
CONCERNS = ("proposed", "ready", "stale", "blocked", "ungrounded", "ambiguous",
            "ratified", "rejected", "deleted")


@dataclass(frozen=True)
class WorklistEntry:
    """One item as a reviewer meets it."""

    uid: str
    type: str
    status: str
    title: str
    concern: str
    ratifiable: bool
    obstacle: str | None
    grounded: bool
    ambiguous: bool
    ratified: bool
    stale: bool
    dead: bool
    depth: int | None


def is_ratified(schema, item) -> bool:
    """Whether ``item`` carries a human's acceptance that still stands. Where
    ratification advances the status (SR-0172) either the status or the record
    says so; where a project has declared it does not, the record is the only
    witness. An item in the suspect role is not settled whatever its record says:
    something it rested on was withdrawn (SR-0175), ratify would accept a signature
    on it again, and a worklist that called it settled would disagree with the
    gate about the one question both answer (SR-0195, SR-0229)."""
    if _is_suspect(schema, item):
        return False
    if item.attrs.get(RATIFIED_BY_ATTR):
        return True
    if not schema.ratify_moves_status:
        return False
    return item.status == schema.status_role("ratified")


def _is_suspect(schema, item) -> bool:
    """Whether ``item`` sits in the suspect role, where a project declares one."""
    try:
        return item.status == schema.status_role("suspect")
    except Exception:  # the role is optional vocabulary; none declared, none held
        return False


def signature_is_stale(schema, item) -> bool:
    """Whether the signature an item carries no longer covers its content. Read
    from the stamp rather than from history, so no subprocess is needed."""
    stamp = item.attrs.get("ratified_fingerprint")
    return bool(stamp) and stamp != fingerprint(item, schema)


def depths_from_roots(project, index: Index | None = None) -> dict[str, int]:
    """How far each item stands from a root along the grounding links — a root is
    0, what grounds directly into it is 1. An interface orders by this to put the
    work closest to the intent first; an item that reaches no root has no depth."""
    schema = project.schema
    idx = index if index is not None else Index.build(project)
    ground = schema.ground_link_types
    depth: dict[str, int] = {}
    queue: deque[str] = deque()
    for item in project.items():
        if item.is_deleted:
            continue
        if schema.is_root(item):
            depth[item.uid] = 0
            queue.append(item.uid)
    while queue:
        cur = queue.popleft()
        nxt = depth[cur] + 1
        for child, _kind in idx.in_links(cur, ground):
            if depth.get(child, nxt + 1) > nxt:
                depth[child] = nxt
                queue.append(child)
    return depth


def entry_for(project, item, *, index: Index | None = None,
              depths: dict[str, int] | None = None) -> WorklistEntry:
    """One item's place in the worklist, with the Tool's own reason when it cannot
    be signed as the graph stands — the same reason ratify would give (SR-0195)."""
    schema = project.schema
    idx = index if index is not None else Index.build(project)
    if depths is None:
        depths = depths_from_roots(project, idx)
    dead = item.status in schema.dead_statuses()
    ambiguous = is_flagged_ambiguous(item)
    grounded = schema.is_root(item) or reaches_root(idx, schema, item.uid)
    ratified = is_ratified(schema, item)
    stale = signature_is_stale(schema, item)
    # Whether a signature can be taken from where the item stands. Where
    # ratification advances the status, that is a transition question; where a
    # project has declared it does not (SR-0172), no transition is involved.
    directly = (transition_refusal(schema, item, schema.status_role("ratified")) is None
                if schema.ratify_moves_status else True)
    obstacle = None if dead else ratification_obstacle(schema, idx, item)
    settled = ratified and not stale
    ratifiable = not dead and obstacle is None

    if dead:
        concern = "deleted" if item.is_deleted else "rejected"
    elif stale:
        # Signed off, then rewritten. Never folded into "ratified": that is the
        # claim the drift contradicts.
        concern = "stale"
    elif settled:
        concern = "ratified"
    elif ambiguous:
        concern = "ambiguous"
    elif not grounded:
        concern = "ungrounded"
    elif not directly:
        concern = "blocked"
    elif item.status == schema.status_role("proposed"):
        concern = "proposed"
    else:
        concern = "ready"

    return WorklistEntry(
        uid=item.uid, type=item.type, status=item.status, title=item.title,
        concern=concern, ratifiable=ratifiable, obstacle=obstacle,
        grounded=grounded, ambiguous=ambiguous, ratified=ratified, stale=stale,
        dead=dead, depth=depths.get(item.uid),
    )


def worklist(project, *, index: Index | None = None,
             include_settled: bool = False) -> list[WorklistEntry]:
    """Everything awaiting a signature, most actionable first, then by depth from
    a root and then by UID.

    Settled and dead items are left out unless asked for, because a reviewer's
    question is what is outstanding; an interface showing everything asks for them.
    """
    idx = index if index is not None else Index.build(project)
    depths = depths_from_roots(project, idx)
    entries = [entry_for(project, item, index=idx, depths=depths)
               for item in project.items()]
    if not include_settled:
        entries = [e for e in entries if e.concern not in ("ratified", "rejected",
                                                           "deleted")]
    order = {name: n for n, name in enumerate(CONCERNS)}
    return sorted(entries, key=lambda e: (order.get(e.concern, len(CONCERNS)),
                                          e.depth if e.depth is not None else 1 << 30,
                                          e.uid))


def ratification_progress(project, *, index: Index | None = None) -> tuple[int, int]:
    """``(accepted, gradable)`` over the live items — the figure a reviewer watches
    climb. An item whose content has moved since it was signed counts as
    outstanding: its signature no longer covers it, and reporting full marks over
    what the gate calls an error stops the reviewer looking."""
    schema = project.schema
    idx = index if index is not None else Index.build(project)
    dead = schema.dead_statuses()
    accepted = gradable = 0
    for item in project.items():
        if item.status in dead:
            continue
        gradable += 1
        if is_ratified(schema, item) and not signature_is_stale(schema, item):
            accepted += 1
    return accepted, gradable
