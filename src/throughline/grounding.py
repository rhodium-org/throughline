# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""The scope-avalanche grounding layer, expressed on the spec format.

Not a separate data model — a set of rules and operations over throughline items,
driven by the ``[grounding]`` table in throughline.toml. Keeps the requirements
graph grounded (every non-root reaches a root) and surfaces the bounded set of
exceptions a human must adjudicate, so unbounded AI generation yields bounded,
ranked review.

Assumption items carry provenance attributes (attrs.owner /
attrs.last_validated / attrs.confidence) alongside their content.
"""
from __future__ import annotations

from typing import NamedTuple

from .fingerprint import fingerprint
from .graph import Index
from .identity import (
    RATIFIED_BY_ATTR,
    RATIFIED_ID_ATTR,
    normalise_identifier,
)
from .model import Item, Link


def reaches_root(idx: Index, schema, uid: str) -> bool:
    """True if ``uid`` grounds upward to a root type over the schema's grounding
    link types. ``schema`` is a :class:`throughline.schema.Schema`."""
    return idx.reaches(uid, schema.is_root, schema.ground_link_types)


# ------------------------------------------------------------------ operations

class GroundingError(ValueError):
    pass


def set_status(schema, item: Item, to: str) -> None:
    """The single choke point for a status change (SR-0130). Every operation
    moves an item through here, so a move the configured [transitions] forbid is
    refused at the source rather than written and caught later by `check`. When a
    project declares no transitions the move is unconstrained, matching the tool's
    other optional vocabularies."""
    if not schema.allows_transition(item.status, to):
        raise GroundingError(
            f"{item.uid}: status change '{item.status}' -> '{to}' is not an "
            "allowed transition")
    item.status = to


def ratification_refusal(schema, idx: Index, item: Item) -> str | None:
    """Why ``item`` may not be signed off, or ``None`` when it may — the two
    states that must not be ratified (scope-avalanche briefing §5).

    Split out of :func:`ratify` so the migration repair that binds an unstamped
    record (SR-0152) can decide what it may legitimately bind using *this*
    implementation rather than a copy of it. A second copy of "what may be
    accepted" is the same drift the ratification record exists to prevent, and
    a repair that ran ahead of these rules would complete records the Tool would
    refuse to write in the first place."""
    if item.attrs.get("ambiguous"):
        return f"{item.uid} is flagged ambiguous and cannot be ratified until clarified"
    if not schema.is_root(item) and not reaches_root(idx, schema, item.uid):
        return f"{item.uid} is not grounded to a root and cannot be ratified"
    return None


def ratify(project, uid: str, by: str, *, index: Index | None = None,
           by_id: str | None = None) -> Item:
    """A human takes accountability. Refused for ambiguous or ungrounded items —
    the two states that must not be signed off (scope-avalanche briefing §5).

    ``index`` lets a caller supply a prebuilt grounding index in place of the one
    built from ``project`` (SR-0151). A composing consumer grounds an item over the
    *union* of its own graph and its sources while writing only to its own
    registers; without this seam such a caller had to reimplement this function's
    body, and a copied accountability record drifts — which is exactly how items
    ratified through throughline-ratify came to carry a signature with no
    fingerprint. The grounding view is the only thing a composing caller may vary:
    every other decision here — what may be signed off, and what gets recorded —
    stays inside this function, so a caller cannot obtain a partial record."""
    item = project.get(uid)
    if item is None:
        raise GroundingError(f"{uid} does not exist")
    schema = project.schema
    idx = index if index is not None else Index.build(project)
    refusal = ratification_refusal(schema, idx, item)
    if refusal is not None:
        raise GroundingError(refusal)
    # Ratifying an already-ratified item whose content has not moved accepts
    # nothing, and would replace the record of who accepted it leaving no trace
    # that it changed (SR-0148). An item ratified before the stamp existed has
    # none to compare against, so that first call is allowed through and stamps it.
    current = fingerprint(item, schema)
    # "Already ratified" is read from whatever this project uses as the durable
    # proof (SR-0172). Where ratification advances the item, that is the status, as
    # it always has been. Where it does not, the status says nothing about sign-off
    # and the record itself is the only honest witness.
    already = (item.status == schema.status_role("ratified")
               if schema.ratify_moves_status
               else item.attrs.get(RATIFIED_BY_ATTR) is not None)
    if already and item.attrs.get("ratified_fingerprint") == current:
        raise GroundingError(
            f"{uid} is already ratified by "
            f"{item.attrs.get('ratified_by', 'a human')} and its content has not "
            "changed since — there is nothing to accept")
    # Advancing is the default, and is transition-validated — an item that cannot
    # legally reach the ratified status is refused rather than moved illegally. A
    # project that binds the ratified role to a workflow state turns this off, and
    # the sign-off is then recorded where the item already stands (SR-0172).
    if schema.ratify_moves_status:
        set_status(schema, item, schema.status_role("ratified"))
    item.attrs[RATIFIED_BY_ATTR] = by
    # A stable identifier for the same human, in its own field and never conflated
    # with the name (SR-0157). Optional, and never invented: a record given none
    # keeps none, and one that had an identifier does not silently lose it when a
    # later ratification is taken without one.
    identifier = normalise_identifier(by_id)
    if identifier is not None:
        item.attrs[RATIFIED_ID_ATTR] = identifier
    item.attrs["ratified_fingerprint"] = current
    return item


class Refusal(NamedTuple):
    """A dependent the cascade could not restatus, and the move that was refused."""
    uid: str
    frm: str
    to: str


class Invalidation(list):
    """The blast radius of an invalidation, carrying what the cascade actually did.

    It *is* the list of reachable dependents (SR-0035), so a caller that reads the
    return as that list is unaffected. What it adds is the partition the operation
    was previously unable to express (SR-0173): ``marked`` holds the dependents
    whose status this run moved to suspect, ``refused`` those whose configured
    lifecycle would not permit the move. Reaching an item and restatusing it are
    different events, and reporting the first as though it were the second is the
    defect these attributes exist to prevent — a dependent already dead is neither,
    since nothing was withheld from it.
    """

    def __init__(self, affected: list[str], marked: list[str],
                 refused: list[Refusal]):
        super().__init__(affected)
        self.marked = marked
        self.refused = refused


def invalidate(project, uid: str, reason: str = "") -> Invalidation:
    """Falsify an assumption (or any node): retire it and mark every transitive
    dependent suspect. Returns the blast radius (SR-0035 reused), which also
    reports which dependents were actually marked and which were refused."""
    item = project.get(uid)
    if item is None:
        raise GroundingError(f"{uid} does not exist")
    schema = project.schema
    idx = Index.build(project)
    # Only along links that carry justification (SR-0159): the project's grounding
    # links, plus any it declared under [grounding] suspect_link_types (SR-0160).
    # The unfiltered impact set is the blast-radius *report* (SR-0035); it answers
    # the wider question of what touches this item, which is not the tool's warrant
    # to restatus. No link type is named here — the set comes from configuration.
    affected = idx.impact(uid, schema.withdrawing_link_types())
    set_status(schema, item, schema.status_role("invalidated"))
    item.attrs["invalidated_reason"] = reason or True
    suspect = schema.status_role("suspect")
    dead = schema.dead_statuses()
    marked: list[str] = []
    refused: list[Refusal] = []
    for aid in affected:
        dep = project.get(aid)
        # A dependent that is already gone is left untouched and is not a refusal:
        # nothing was withheld from an item that has already been retired.
        if dep is None or dep.status in dead:
            continue
        # A move the project's lifecycle does not declare is refused here rather
        # than written (SR-0130). The refusal is recorded, not swallowed: an item
        # whose footing was withdrawn but which carries no flag is exactly the
        # drift suspicion exists to surface (SR-0173).
        if not schema.allows_transition(dep.status, suspect):
            refused.append(Refusal(aid, dep.status, suspect))
            continue
        dep.status = suspect
        reasons = dep.attrs.setdefault("suspect_reasons", [])
        reasons.append(f"upstream {uid} invalidated")
        marked.append(aid)
    return Invalidation(affected, marked, refused)


def scout_ingest(project, report: dict) -> dict:
    """Fold a scout report in. Scout PROPOSES; humans RATIFY.

    report = {proposed_roots:[{id,type,title,rationale}],
              ambiguities:[{id,reason}], coverage_gaps:[{root,detail}]}
    Returns a summary and the Registers that changed (for write-back).
    """
    summary = {"roots_proposed": [], "ambiguities_flagged": [], "gaps": [], "touched": set()}

    # proposed roots need a home register; use the first register whose prefix
    # looks right, else the first register in the project.
    for r in report.get("proposed_roots", []):
        if project.get(r["id"]) is not None:
            continue
        reg = project.register_of(r["id"]) or next(iter(project.registers.values()), None)
        if reg is None:
            continue
        item = Item(uid=r["id"], type=r.get("type", "business_need"),
                    status=project.schema.status_role("proposed"),
                    title=r.get("title", ""), text=r.get("rationale", ""))
        item.attrs["origin"] = "ai"
        item._register_prefix = reg.prefix
        reg.items[item.uid] = item
        summary["roots_proposed"].append(item.uid)
        summary["touched"].add((reg.prefix, item.uid))

    for a in report.get("ambiguities", []):
        item = project.get(a["id"])
        if not item:
            continue
        item.attrs["ambiguous"] = True
        item.attrs.setdefault("suspect_reasons", []).append(f"scout: {a['reason']}")
        suspect = project.schema.status_role("suspect")
        if item.status == project.schema.status_role("ratified") \
                and project.schema.allows_transition(item.status, suspect):
            item.status = suspect
        summary["ambiguities_flagged"].append(item.uid)
        summary["touched"].add((item._register_prefix, item.uid))

    for gap in report.get("coverage_gaps", []):
        summary["gaps"].append((gap.get("root"), gap.get("detail")))

    return summary


def link(item: Item, target: str, kind: str) -> None:
    item.links.append(Link(target=target, type=kind))
