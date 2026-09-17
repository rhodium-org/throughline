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
    RATIFICATION_ATTRS,
    RATIFIED_BY_ATTR,
    RATIFIED_ID_ATTR,
    WITHDRAWN_BY_ATTR,
    WITHDRAWN_ID_ATTR,
    WITHDRAWN_RATIFIER_ATTR,
    WITHDRAWN_REASON_ATTR,
    normalise_identifier,
)
from .model import Item, Link


def reaches_root(idx: Index, schema, uid: str) -> bool:
    """True if ``uid`` grounds upward to a root type over the schema's grounding
    link types. ``schema`` is a :class:`throughline.schema.Schema`."""
    return idx.reaches(uid, schema.is_root, schema.ground_link_types)


def grounding_gap(schema, idx: Index, item: Item) -> str | None:
    """Why ``item`` is not grounded, in the words the gate's `orphan` finding uses,
    or ``None`` when it is. A root is grounded by definition. Shared by the gate
    and by the link operations, so a change is refused by exactly the rule that
    would have reported it (SR-0211)."""
    if schema.is_root(item):
        return None
    if not idx.out_links(item.uid, schema.ground_link_types):
        return f"{item.type} has no grounding link — nothing justifies it"
    if not reaches_root(idx, schema, item.uid):
        return "grounding chain never reaches a root"
    return None


def is_unserved(schema, idx: Index, item: Item) -> bool:
    """True when ``item`` is a delivery root that nothing grounds into, the
    gate's `unserved-root` finding."""
    return (item.type in schema.delivery_roots
            and not idx.in_links(item.uid, schema.ground_link_types))


def ungrounded_by(schema, items, before: Index, after: Index) -> tuple[list[str], list[str]]:
    """The items a change from ``before`` to ``after`` would leave reaching no root,
    and the delivery roots it would leave served by nothing (SR-0211). Measured as
    a difference, so an item already ungrounded before the change is not blamed on
    it and a graph red elsewhere does not block an unrelated change."""
    orphaned: list[str] = []
    unserved: list[str] = []
    for item in items:
        if item.is_deleted:
            continue
        if grounding_gap(schema, before, item) is None \
                and grounding_gap(schema, after, item) is not None:
            orphaned.append(item.uid)
        if not is_unserved(schema, before, item) and is_unserved(schema, after, item):
            unserved.append(item.uid)
    return sorted(orphaned), sorted(unserved)


# ------------------------------------------------------------------ operations

class GroundingError(ValueError):
    pass


def transition_refusal(schema, item: Item, to: str) -> str | None:
    """Why ``item`` may not move to status ``to``, or ``None`` when it may. The one
    wording for a move the configured [transitions] forbid, asked by
    :func:`set_status` when it writes and by :func:`ratification_obstacle` before
    anything is written (SR-0130, SR-0195)."""
    if not schema.allows_transition(item.status, to):
        return (f"{item.uid}: status change '{item.status}' -> '{to}' is not an "
                "allowed transition")
    return None


def set_status(schema, item: Item, to: str) -> None:
    """The single choke point for a status change (SR-0130). Every operation
    moves an item through here, so a move the configured [transitions] forbid is
    refused at the source rather than written and caught later by `check`. When a
    project declares no transitions the move is unconstrained, matching the tool's
    other optional vocabularies."""
    refusal = transition_refusal(schema, item, to)
    if refusal is not None:
        raise GroundingError(refusal)
    item.status = to


# The attribute the Tool reads as "this item's wording is in doubt" (SR-0221,
# SR-0222). Named once, because the gate, ratify, flag and clarify must all mean
# the same attribute by it.
AMBIGUOUS_ATTR = "ambiguous"

# The clarification record (SR-0213): who removed an item's ambiguity flag, why,
# and what check reported for the flag at that moment. Held once, as the withdrawal
# record is — a later clarification of a flag raised again replaces it, and the
# succession is recoverable from version control.
CLARIFIED_BY_ATTR = "clarified_by"
CLARIFIED_REASON_ATTR = "clarified_reason"
CLARIFIED_AMBIGUITY_ATTR = "clarified_ambiguity"

# Guarded like the ratification record (SR-0170): a hand-written one would claim
# that somebody judged an ambiguity resolved when nobody did.
CLARIFICATION_ATTRS = {
    CLARIFIED_BY_ATTR: "clarify",
    CLARIFIED_REASON_ATTR: "clarify",
    CLARIFIED_AMBIGUITY_ATTR: "clarify",
}


def attribute_owner(name: str) -> tuple[str, str] | None:
    """The record ``name`` belongs to and the one command that owns it, or ``None``
    for an attribute no single command owns. Every operation that sets or removes
    attributes generally asks this, so it refuses the same things (SR-0170,
    SR-0213)."""
    owner = RATIFICATION_ATTRS.get(name)
    if owner is not None:
        return "ratification record", owner
    owner = CLARIFICATION_ATTRS.get(name)
    if owner is not None:
        return "clarification record", owner
    return None


def is_flagged_ambiguous(item: Item) -> bool:
    """True while ``item`` carries the ambiguity flag. The one predicate the gate,
    ratify and clarify read, so an item is flagged for all of them or for none."""
    return bool(item.attrs.get(AMBIGUOUS_ATTR))


def ambiguity_report(item: Item) -> str:
    """What check reports for ``item``'s ambiguity flag. `tl clarify` copies this
    into its record, so the record holds the words the person removing the flag was
    shown (SR-0213).

    A flag raised when the item was created carries no reason (SR-0223), which left
    the author nothing to act on, so the report names the gap and the operation that
    fills it (SR-0222)."""
    recorded = "; ".join(item.attrs.get("suspect_reasons", []))
    return recorded or (
        f"flagged ambiguous with no reason recorded — `tl flag {item.uid} "
        "--reason ...` records one")


def ambiguity_change_refusal(item: Item, to) -> str | None:
    """Why ``item``'s ambiguity flag may not be set to ``to``, or ``None`` when it
    may (SR-0213). Only `tl clarify` removes the flag, because removing it is a
    judgement that the ambiguity is resolved and that judgement is recorded; a
    value that no longer flags the item would remove it with no record at all."""
    if is_flagged_ambiguous(item) and not to:
        return (f"{item.uid} is flagged ambiguous, and the flag is removed only by "
                "`tl clarify`, which records who judged the ambiguity resolved "
                "and why")
    return None


def ratification_refusal(schema, idx: Index, item: Item) -> str | None:
    """Why ``item`` may not be signed off, or ``None`` when it may — the two
    states that must not be ratified (scope-avalanche briefing §5).

    Split out of :func:`ratify` so the migration repair that binds an unstamped
    record (SR-0152) can decide what it may legitimately bind using *this*
    implementation rather than a copy of it. A second copy of "what may be
    accepted" is the same drift the ratification record exists to prevent, and
    a repair that ran ahead of these rules would complete records the Tool would
    refuse to write in the first place."""
    if is_flagged_ambiguous(item):
        return f"{item.uid} is flagged ambiguous and cannot be ratified until clarified"
    if not schema.is_root(item) and not reaches_root(idx, schema, item.uid):
        return f"{item.uid} is not grounded to a root and cannot be ratified"
    return None


def origin_change_refusal(schema, item: Item, to: str | None) -> str | None:
    """Why ``item``'s origin may not become ``to`` (``None`` meaning removed), or
    ``None`` when it may (SR-0208).

    The unratified gate recognises a machine-authored item by its origin (SR-0092,
    SR-0149), so moving an origin out of the machine-origin set passes the gate
    without anyone signing. Every operation that sets or removes an attribute asks
    this one predicate. A move within the set, or into it, keeps the item under
    the gate and is allowed."""
    origin = item.attrs.get("origin")
    if origin in schema.ai_origins and to not in schema.ai_origins:
        now = "removed" if to is None else f"changed to '{to}'"
        return (f"{item.uid} is machine-authored (origin '{origin}') and its origin "
                f"cannot be {now} — a machine-authored item is accepted by "
                "`tl ratify`, not by relabelling it")
    return None


def attribute_removal_refusal(schema, item: Item, name: str) -> str | None:
    """Why ``name`` may not be removed from ``item``, or ``None`` when it may
    (SR-0206). `tl amend --unset` and `tl schema attr remove --unset` both ask
    this, so they refuse the same things (SR-0207).

    Removal reaches an attribute whether or not the type still declares it, since
    a withdrawn attribute is exactly what it exists to clear. It stops at the
    attributes a gate reads: the ratification record keeps the owners SR-0170
    gives it, an origin stays under SR-0208, and the ambiguity flag and the record
    of its removal belong to `tl clarify` (SR-0213)."""
    if name not in item.attrs:
        return f"{item.uid} carries no attribute '{name}'"
    owned = attribute_owner(name)
    if owned is not None:
        record, owner = owned
        return (f"'{name}' on {item.uid} is part of the {record} and "
                f"cannot be removed — `tl {owner}` owns it")
    # Only a value that flags the item is the flag; a false one blocks nothing.
    if name == AMBIGUOUS_ATTR and is_flagged_ambiguous(item):
        return (f"'ambiguous' marks {item.uid} as unable to be ratified until it is "
                "clarified, and removing the flag does not clarify it — "
                "`tl clarify` removes it and records who judged it resolved and why")
    if name == "origin":
        refusal = origin_change_refusal(schema, item, None)
        if refusal is not None:
            return refusal
    spec = schema.attr(item.type, name)
    if spec is not None and spec.required:
        return (f"'{item.type}' declares '{name}' as required — set another value "
                "with --attr, or withdraw the attribute from the type first")
    return None


def ratification_obstacle(schema, idx: Index, item: Item, *,
                          replacing: bool = False) -> str | None:
    """Why :func:`ratify` would refuse ``item`` as the graph now stands, or ``None``
    when it would proceed — the whole precondition set, asked without writing
    anything (SR-0195).

    ratify must refuse an item it cannot accept *before* a front end renders it or
    asks anyone to confirm it, so "may this be signed?" has to be answerable ahead
    of the act. :func:`ratify` answers it through this same function rather than
    repeating the conditions, so the refusal a user is shown early is by
    construction the refusal the write would have raised. It is a superset of
    :func:`ratification_refusal`, which stays the narrower "must never be signed"
    predicate the migration repair binds against (SR-0152) — an unstamped record
    is exactly what that repair exists to complete, so it must not be told that an
    already-ratified item has nothing to accept."""
    refusal = ratification_refusal(schema, idx, item)
    if refusal is not None:
        return refusal
    already = (item.status == schema.status_role("ratified")
               if schema.ratify_moves_status
               else item.attrs.get(RATIFIED_BY_ATTR) is not None)
    if already and item.attrs.get("ratified_fingerprint") == fingerprint(item, schema):
        # A correction is the one signature over unchanged content that accepts
        # something — the identity on the record (SR-0196). Whether it is allowed
        # rests on the record being unpublished, which only :func:`ratify` can
        # establish, so the refusal is lifted here and reimposed there.
        if not replacing:
            return (f"{item.uid} is already ratified by "
                    f"{item.attrs.get('ratified_by', 'a human')} and its content has "
                    "not changed since — there is nothing to accept; pass "
                    "--replacing to correct the recorded ratifier instead")
    # The move :func:`ratify` makes is asked about here too, so a status the
    # transitions do not let reach the ratified role is refused before anything is
    # rendered or asked (SR-0195) and before any item in a run is written
    # (SR-0199). Only where ratification moves the status: where a project declares
    # it does not (SR-0172), no move is made and nothing can be in the way.
    if schema.ratify_moves_status:
        refusal = transition_refusal(schema, item, schema.status_role("ratified"))
        if refusal is not None:
            return refusal
    return None


def ratify(project, uid: str, by: str, *, index: Index | None = None,
           by_id: str | None = None, replacing: bool = False) -> Item:
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
    # Every reason this may be refused, including that an already-ratified item
    # whose content has not moved accepts nothing and would replace the record of
    # who accepted it leaving no trace that it changed (SR-0148). An item ratified
    # before the stamp existed has none to compare against, so that first call is
    # allowed through and stamps it.
    obstacle = ratification_obstacle(schema, idx, item, replacing=replacing)
    if obstacle is not None:
        raise GroundingError(obstacle)
    # A correction replaces the identity on a record that was never published
    # (SR-0196). The test is made here rather than left to the caller, for the
    # reason every other decision in this function is: a front end that could
    # assert its way past it would be able to obtain a record this function would
    # refuse to write. Unestablished is refused, not assumed — a correction is
    # permitted on evidence the record was never shared, and absence of evidence
    # is not that evidence.
    superseded = None
    if replacing:
        from .ratification import SUPERSEDED_ATTR, ratification_is_committed
        published = ratification_is_committed(project, item)
        if published is None:
            raise GroundingError(
                f"{uid}: cannot establish whether this ratification has been "
                "committed, so it may not be replaced — a correction is only "
                "allowed for a record that was never published")
        if published:
            raise GroundingError(
                f"{uid} was ratified by {item.attrs.get(RATIFIED_BY_ATTR)} in a "
                "commit, so that record may not be replaced: a published "
                "signature is not something one caller may take from another")
        superseded = item.attrs.get(RATIFIED_BY_ATTR)
    current = fingerprint(item, schema)
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
    # Kept so a correction never reads as the original record (SR-0148's condition
    # that an accountability record never changes without the graph showing it).
    if superseded is not None and superseded != by:
        from .ratification import SUPERSEDED_ATTR
        item.attrs[SUPERSEDED_ATTR] = superseded
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


def withdraw(project, uids, *, by: str, reason: str,
             by_id: str | None = None) -> list[Item]:
    """Withdraw the ratification of each item in ``uids`` (SR-0197): the signature
    no longer stands, the item returns to those awaiting a human, and the record
    says who withdrew it, why, and whose signature it was.

    Not invalidation, and deliberately its opposite in effect: ``tl invalidate``
    says an item is false and cascades suspicion into everything grounded on it
    (SR-0035); this says nothing about the item. Its words have not moved, so
    nothing resting on them has lost its footing — content is untouched and no
    dependent is restatused. The item moves to the status bound to the suspect
    role (SR-0131), which every live status may reach (SR-0175) and which already
    means what this needs: a human must look again.

    Anyone may withdraw, because withdrawal takes nothing — the item cannot count
    as ratified again until a real human accepts it — but the act is recorded, not
    silent: stripping a colleague's sign-off and leaving an item that merely looks
    unratified would be the quieter attack on an accountability record.

    All or nothing: every item is checked before any is written, so a mistyped UID
    in a batch is a failure the caller sees, never a half-applied run. Refused for
    an item that carries no ratification, for an empty reason, and for a status the
    project's lifecycle will not let move to suspect.
    """
    if not by or not by.strip():
        raise GroundingError("a withdrawal must name who is withdrawing it")
    if not reason or not reason.strip():
        raise GroundingError("a withdrawal must state its reason — pass --reason")
    identifier = normalise_identifier(by_id)          # IdentityError if malformed
    schema = project.schema
    suspect = schema.status_role("suspect")
    items: list[Item] = []
    seen: set[str] = set()
    for uid in uids:
        if uid in seen:
            raise GroundingError(f"{uid} is named more than once")
        seen.add(uid)
        item = project.get(uid)
        if item is None:
            raise GroundingError(f"{uid} does not exist")
        if not item.attrs.get(RATIFIED_BY_ATTR):
            raise GroundingError(f"{uid} carries no ratification to withdraw")
        if not schema.allows_transition(item.status, suspect):
            raise GroundingError(
                f"{uid}: status change '{item.status}' -> '{suspect}' is not an "
                "allowed transition, so its ratification cannot be withdrawn")
        items.append(item)
    # The record the ratifying verbs own is cleared, and only that record: the
    # withdrawn identity moves into the withdrawal record rather than vanishing,
    # so the graph still shows that a signature was taken and then set aside.
    cleared = [name for name, owner in RATIFICATION_ATTRS.items()
               if owner != "withdraw"]
    for item in items:
        ratifier = item.attrs[RATIFIED_BY_ATTR]
        for name in cleared:
            item.attrs.pop(name, None)
        item.attrs[WITHDRAWN_RATIFIER_ATTR] = ratifier
        item.attrs[WITHDRAWN_BY_ATTR] = by
        if identifier is not None:
            item.attrs[WITHDRAWN_ID_ATTR] = identifier
        else:
            item.attrs.pop(WITHDRAWN_ID_ATTR, None)
        item.attrs[WITHDRAWN_REASON_ATTR] = reason
        set_status(schema, item, suspect)
        item.attrs.setdefault("suspect_reasons", []).append(
            f"ratification by {ratifier} withdrawn by {by}: {reason}")
    return items


def clarification_refusal(project, uid: str) -> str | None:
    """Why the item ``uid`` cannot be clarified, or ``None`` when it can. Asked by
    the command before it asks anyone for a reason or a name, and by
    :func:`clarify` itself, so the refusal a user sees early is the one the write
    would have raised."""
    item = project.get(uid)
    if item is None:
        return f"{uid} does not exist"
    if item.is_deleted:
        return f"{uid} is deleted — a tombstone is permanent (SR-0093)"
    if not is_flagged_ambiguous(item):
        return f"{uid} carries no ambiguity flag to remove"
    return None


def clarify(project, uid: str, *, by: str, reason: str) -> Item:
    """Remove ``uid``'s ambiguity flag and record who judged the ambiguity resolved,
    why, and what check reported for the flag (SR-0213).

    Its own verb, not a mode of amend: removing the flag is a judgement, whether
    the item was reworded or the flag was wrong, and a judgement is recorded with a
    name and a reason, as a withdrawal is. Nothing else on the item moves: a
    rewording was made by `tl amend`, which reports what it made stale, and an
    item that scout ingestion moved to suspect goes back through `tl ratify`, which
    shows it before anyone signs.

    Anyone may clarify, because clarifying accepts nothing: a machine-authored item
    still needs a human to ratify it, and ratify shows that human this record
    (SR-0214). One item per call, because each flag carries its own reasons.
    """
    if not by or not by.strip():
        raise GroundingError("a clarification must name who judged the ambiguity "
                             "resolved — pass --by")
    if not reason or not reason.strip():
        raise GroundingError("a clarification must state why the ambiguity is "
                             "resolved — pass --reason")
    refusal = clarification_refusal(project, uid)
    if refusal is not None:
        raise GroundingError(refusal)
    item = project.get(uid)
    reported = ambiguity_report(item)
    del item.attrs[AMBIGUOUS_ATTR]
    item.attrs[CLARIFIED_BY_ATTR] = by
    item.attrs[CLARIFIED_REASON_ATTR] = reason
    item.attrs[CLARIFIED_AMBIGUITY_ATTR] = reported
    return item


def flag_refusal(project, uid: str) -> str | None:
    """Why the item ``uid`` cannot be flagged, or ``None`` when it can. Asked by the
    command before it asks anyone for a reason or a name, and by :func:`flag`
    itself, so the refusal a user sees early is the one the write would have
    raised."""
    item = project.get(uid)
    if item is None:
        return f"{uid} does not exist"
    if item.is_deleted:
        return f"{uid} is deleted — a tombstone is permanent (SR-0093)"
    return None


def flag(project, uid: str, *, by: str, reason: str) -> Item:
    """Flag ``uid`` as ambiguous, recording who flagged it and why (SR-0221).

    The counterpart of :func:`clarify`. Flagging is a judgement about someone
    else's words and it blocks their ratification, so it is recorded with a name
    and a reason — and the reason is what the author needs in order to fix the
    wording. Both go into the reasons recorded against the item, where check
    already reads them (SR-0222) and where clarify copies them from.

    Nothing else on the item moves. Scout ingestion moved a ratified item to
    suspect, which forces a new signature even where the flag turns out to be
    wrong; the flag already fails the gate and blocks ratification, and a rewording
    breaks the stamp on its own. An item already flagged keeps its flag and gains
    the reason, because two reviewers can have different doubts.
    """
    if not by or not by.strip():
        raise GroundingError("a flag must name who is flagging the item — pass --by")
    if not reason or not reason.strip():
        raise GroundingError("a flag must state why the wording is ambiguous — "
                             "pass --reason")
    refusal = flag_refusal(project, uid)
    if refusal is not None:
        raise GroundingError(refusal)
    item = project.get(uid)
    item.attrs[AMBIGUOUS_ATTR] = True
    item.attrs.setdefault("suspect_reasons", []).append(
        f"flagged ambiguous by {by}: {reason}")
    return item


def link(item: Item, target: str, kind: str) -> None:
    item.links.append(Link(target=target, type=kind))
