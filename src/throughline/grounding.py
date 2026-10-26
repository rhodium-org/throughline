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

from dataclasses import dataclass, field

from .fingerprint import fingerprint
from .signatures import (
    AT, BY, CONTENT, ID, LINKS, ROLE, STAMP, SUPERSEDES,
    W_AT, W_BY, W_ID, W_OF, W_OF_ID, W_REASON,
    describe, entry_of, is_stale, latest, names, now_iso, relevant,
    requirement_met, retired, signatures, signed_record, stale_entries, stamp_of,
    standing,
)
from .signed_links import links_moved, links_record, signed_link_types
from .graph import Index
from .identity import RATIFICATION_ATTRS, normalise_identifier
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
    # A tombstone is signed for its deletion, not its words (SR-0269): that the
    # item was ambiguous or ungrounded is no reason to keep it, and may be the
    # reason it goes.
    if item.is_deleted:
        if (item.deleted or {}).get("outright"):
            return (f"{item.uid} was retired outright — nothing signed was undone, "
                    "so there is no deletion to ratify (SR-0272)")
        return None
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
    # Ownership first (SR-0170): a guarded name is refused by its owner whether or
    # not the item carries it, so the record's names are never reported as a mere
    # absence — since format 4 the record is not under attrs at all (SR-0254).
    owned = attribute_owner(name)
    if owned is not None:
        record, owner = owned
        return (f"'{name}' on {item.uid} is part of the {record} and "
                f"cannot be removed — `tl {owner}` owns it")
    if name not in item.attrs:
        return f"{item.uid} carries no attribute '{name}'"
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
                          replacing: bool = False, by: str | None = None,
                          by_id: str | None = None) -> str | None:
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
    # An item the workflow has carried past the ratified status — it holds a
    # record, it is live, and the ratified status cannot be reached from where it
    # stands — is already ratified for every purpose here (SR-0237). A suspect
    # item is not: the ratified status is reachable, and re-signing it is how a
    # human confirms it again.
    ratified_status = schema.status_role("ratified")
    past = (schema.ratify_moves_status
            and bool(signatures(item))
            and item.status not in schema.dead_statuses()
            and transition_refusal(schema, item, ratified_status) is not None)
    unchanged = (" and signed links have " if signed_link_types(item, schema)
                 else " has ") + "not changed since — there is nothing to accept"
    # A suspect item is never "already ratified": something it rested on was
    # withdrawn, and re-signing it is how a human confirms it again (SR-0175).
    try:
        suspect = item.status == schema.status_role("suspect")
    except Exception:                       # the role is optional vocabulary
        suspect = False
    # A signature over content nobody has changed accepts nothing (SR-0148). Which
    # signature that is depends on who is asking: a named person renews their own
    # (SR-0259), so theirs is the one judged; in a project that declares no roles
    # one signature is all an item takes, so a second person adding theirs over
    # unchanged content accepts nothing either, as before. A caller naming nobody
    # — the worklist — asks whether anything is left for anyone to sign. The one
    # exception is a correction, which accepts the identity on the record
    # (SR-0196); whether it is allowed rests on the record being unpublished,
    # which only :func:`ratify` can establish, so the refusal is lifted here and
    # reimposed there.
    if not replacing and not suspect:
        if by or by_id:
            own = entry_of(item, by=by, by_id=by_id)
            if own is not None:
                if not is_stale(item, schema, own):
                    return (f"{item.uid} is already signed by {describe(own)} and its "
                            f"content{unchanged}; pass --replacing to correct the "
                            "recorded ratifier instead")
            elif not schema.roles and standing(item, schema).met:
                return (f"{item.uid} is already ratified by {names(item)} and its "
                        f"content{unchanged}; pass --replacing to correct the "
                        "recorded ratifier instead")
        elif standing(item, schema).met and not stale_entries(item, schema):
            return (f"{item.uid} is already ratified by {names(item)} and its "
                    f"content{unchanged}; pass --replacing to correct the recorded "
                    "ratifier instead")
    # The move :func:`ratify` makes is asked about here too, so a status the
    # transitions do not let reach the ratified role is refused before anything is
    # rendered or asked (SR-0195) and before any item in a run is written
    # (SR-0199). Only where ratification moves the status: where a project declares
    # it does not (SR-0172), no move is made and nothing can be in the way. Nor is
    # anything in the way of a second signature: an item that already holds a
    # record and has been carried past the ratified status is signed where it
    # stands, because it is the record that went stale, not the status (SR-0237).
    # A tombstone is signed where it stands (SR-0269): ratifying a deletion
    # retires the item, it does not move it to the ratified status.
    if schema.ratify_moves_status and not past and not item.is_deleted:
        refusal = transition_refusal(schema, item, ratified_status)
        if refusal is not None:
            return refusal
    return None


def ratify(project, uid: str, by: str, *, index: Index | None = None,
           by_id: str | None = None, replacing: bool = False,
           role: str | None = None, at: str | None = None,
           cascade: "Cascade | None" = None) -> Item:
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
    stays inside this function, so a caller cannot obtain a partial record.

    ``role`` is the role the person signs in (SR-0259): required, and checked
    against the declared holders by identifier, in a project that declares roles;
    refused in one that does not. ``at`` is the moment recorded (SR-0256), taken
    from the clock when not given — a parameter so a host without a clock of its
    own, or a test, can supply it.

    ``cascade`` receives what ratifying a tombstone does to its dependents
    (SR-0270): the signature that retires an item marks every transitive dependent
    along the withdrawing link types suspect, by the walk `invalidate` makes, and
    the caller writes those items as it writes this one. A caller that supplies
    none is refused a deletion that has live dependents, before anything is
    signed: a front end built before this rule must fail loudly rather than sign a
    deletion whose consequences it never writes."""
    item = project.get(uid)
    if item is None:
        raise GroundingError(f"{uid} does not exist")
    schema = project.schema
    idx = index if index is not None else Index.build(project)
    # A stable identifier for the same human, in its own field and never conflated
    # with the name (SR-0157). Optional where the project declares no roles, and
    # never invented. Where it does, the identifier is what a role is checked
    # against (SR-0259): a name proves nothing.
    identifier = normalise_identifier(by_id)
    role = (role or "").strip() or None
    if schema.roles:
        declared = ", ".join(schema.roles)
        if role is None:
            raise GroundingError(
                f"{uid}: this project declares roles ({declared}), so a signature "
                "names the role it is given in — pass --as <role>")
        if role not in schema.roles:
            raise GroundingError(
                f"{uid}: '{role}' is not a role this project declares — the roles "
                f"are {declared}")
        if identifier is None:
            raise GroundingError(
                f"{uid}: a signature in a role needs the signer's stable identifier "
                "— pass --by-id, e.g. email:ada@example.com (on a terminal, ratify "
                "offers the email this repository signs commits with)")
        if identifier not in schema.roles[role]:
            held = schema.roles_of(identifier)
            raise GroundingError(
                f"{uid}: {identifier} does not hold the role '{role}'"
                + (f" — it holds {', '.join(held)}" if held
                   else " — it holds no role in this project"))
    elif role is not None:
        raise GroundingError(
            f"{uid}: this project declares no roles, so a signature cannot name one "
            "— leave out --as, or declare roles under [ratify.roles]")
    # Every reason this may be refused, including that a signature over content
    # nobody has changed accepts nothing and would replace the record of who
    # accepted it leaving no trace that it changed (SR-0148). An item ratified
    # before the stamp existed has none to compare against, so that first call is
    # allowed through and stamps it.
    obstacle = ratification_obstacle(schema, idx, item, replacing=replacing,
                                     by=by, by_id=identifier)
    if obstacle is not None:
        raise GroundingError(obstacle)
    # Refused before the signature, not after it (SR-0270): the dependents a
    # retirement marks must reach a writer.
    affected: list[str] = []
    if item.is_deleted:
        affected = live_dependents(project, idx, uid)
        if affected and cascade is None:
            raise GroundingError(
                f"{uid}: ratifying its deletion marks {len(affected)} dependent(s) "
                f"suspect ({', '.join(affected)}), which this caller does not "
                "receive to write — pass `cascade=` to ratify (SR-0270)")
    # A correction replaces the identity on the latest signature, where that
    # signature was never published (SR-0196). The test is made here rather than
    # left to the caller, for the reason every other decision in this function is:
    # a front end that could assert its way past it would be able to obtain a
    # record this function would refuse to write. Unestablished is refused, not
    # assumed — a correction is permitted on evidence the record was never shared,
    # and absence of evidence is not that evidence.
    superseded = None
    if replacing:
        from .ratification import signature_is_committed
        corrected = latest(item)
        if corrected is None:
            raise GroundingError(f"{uid} carries no signature to correct")
        published = signature_is_committed(project, item, corrected)
        if published is None:
            raise GroundingError(
                f"{uid}: cannot establish whether this ratification has been "
                "committed, so it may not be replaced — a correction is only "
                "allowed for a record that was never published")
        if published:
            raise GroundingError(
                f"{uid} was ratified by {corrected.get(BY)} in a "
                "commit, so that record may not be replaced: a published "
                "signature is not something one caller may take from another")
        superseded = corrected.get(BY)
        item.ratifications.remove(corrected)
    # The fingerprint of the content, or of the deletion record for a tombstone
    # (SR-0269): status is outside the content fingerprint, so a signature over
    # the words would say nothing about whether the item should go.
    current = stamp_of(item, schema)
    entry: dict = {BY: by}
    if identifier is not None:
        entry[ID] = identifier
    if role is not None:
        entry[ROLE] = role
    # When (SR-0256): UTC, to the second, from the clock of the machine writing it.
    entry[AT] = at or now_iso()
    entry[STAMP] = current
    # The words the stamp was taken over, beside it (SR-0215). A digest proves only
    # that content moved; this is what lets anyone show what the person agreed to,
    # with no version control at all — which a browser holding one commit, a
    # shallow clone and rewritten history all lack.
    entry[CONTENT] = signed_record(item, schema)
    # The links the signature covers, beside it (SR-0243). An item that carries
    # none gets no link record, and a deletion signature covers no links.
    links = [] if item.is_deleted else links_record(item, schema)
    if links:
        entry[LINKS] = links
    # Kept so a correction never reads as the original record (SR-0148's condition
    # that an accountability record never changes without the graph showing it).
    if superseded is not None and superseded != by:
        entry[SUPERSEDES] = superseded
    # A person signs an item once (SR-0259): an earlier signature of theirs goes
    # and this one stands in its place, never another person's. An identifier the
    # earlier one held is not silently lost when the new one was given none.
    own = entry_of(item, by=by, by_id=identifier)
    if own is not None:
        if ID not in entry and own.get(ID):
            entry[ID] = own[ID]
        item.ratifications.remove(own)
    # A project that declares no roles has one signature slot, as it always had:
    # a signature over content that has moved stands in place of the stale ones,
    # whoever gave them, exactly as the flat record was replaced before format 4.
    # Where roles are declared each person's accountability is their own, so a
    # stale signature stays, named by the gate, until its signer renews or
    # withdraws it (SR-0261).
    if not schema.roles:
        item.ratifications = [e for e in item.ratifications
                              if not is_stale(item, schema, e)]
    # An entry naming nobody is not a signature (SR-0217): ratifying again is the
    # route the gate names for clearing one, so this is where it goes.
    item.ratifications = [e for e in item.ratifications if e.get(BY)]
    item.ratifications.append(entry)
    # Advancing is the default, and is transition-validated — an item that cannot
    # legally reach the ratified status is refused rather than moved illegally. A
    # project that binds the ratified role to a workflow state turns this off, and
    # the sign-off is then recorded where the item already stands (SR-0172). The
    # status moves only once the signatures meet what the item's kind requires
    # (SR-0260); until then they are recorded and the item stays where it is. A
    # re-signature over amended content on an item the workflow has carried past
    # the ratified status is recorded where the item stands (SR-0237); the obstacle
    # check above has already refused every first signature that cannot get there.
    if item.is_deleted:
        # The signature that brings the deletion to the standing its kind requires
        # retires the item, and a retired item withdraws the footing of everything
        # grounded on it exactly as a falsified one does (SR-0270): the one walk
        # serves both. The status does not move — the tombstone stands.
        if requirement_met(item, schema) and affected:
            cascade_suspicion(project, idx, uid, f"upstream {uid} deleted",
                              into=cascade)
        return item
    if schema.ratify_moves_status and requirement_met(item, schema):
        target = schema.status_role("ratified")
        if transition_refusal(schema, item, target) is None:
            set_status(schema, item, target)
    return item


class Refusal(NamedTuple):
    """A dependent the cascade could not restatus, and the move that was refused."""
    uid: str
    frm: str
    to: str


@dataclass
class Cascade:
    """What one walk of suspicion did (SR-0173, SR-0270): the dependents reached
    along the withdrawing link types, the ones whose status this run moved to
    suspect, and the ones whose configured lifecycle would not permit the move.
    Shared by `invalidate` and by the ratification of a deletion, which fills the
    one its caller passes so the caller can write what moved."""
    affected: list[str] = field(default_factory=list)
    marked: list[str] = field(default_factory=list)
    refused: list[Refusal] = field(default_factory=list)


def live_dependents(project, idx: Index, uid: str) -> list[str]:
    """The transitive dependents of ``uid`` along the withdrawing link types that
    are still live (SR-0159): the ones a withdrawal of ``uid``'s footing reaches."""
    schema = project.schema
    dead = schema.dead_statuses()
    out = []
    for aid in idx.impact(uid, schema.withdrawing_link_types()):
        dep = project.get(aid)
        if dep is None or dep.status in dead:
            continue
        out.append(aid)
    return out


def cascade_suspicion(project, idx: Index, uid: str, note: str, *,
                      into: Cascade | None = None) -> Cascade:
    """Mark every transitive dependent of ``uid`` along the withdrawing link types
    suspect (SR-0035, SR-0159), recording the ones the lifecycle refuses (SR-0173).
    ``note`` is what each dependent's ``suspect_reasons`` records. Fills ``into``
    when given, so a caller that accumulates can read what moved.

    Only along links that carry justification: the project's grounding links,
    plus any it declared under [grounding] suspect_link_types (SR-0160). The
    unfiltered impact set is the blast-radius *report* (SR-0035); it answers the
    wider question of what touches this item, which is not the tool's warrant to
    restatus. No link type is named here — the set comes from configuration."""
    schema = project.schema
    result = into if into is not None else Cascade()
    affected = idx.impact(uid, schema.withdrawing_link_types())
    result.affected.extend(affected)
    suspect = schema.status_role("suspect")
    dead = schema.dead_statuses()
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
            result.refused.append(Refusal(aid, dep.status, suspect))
            continue
        dep.status = suspect
        reasons = dep.attrs.setdefault("suspect_reasons", [])
        reasons.append(note)
        result.marked.append(aid)
    return result


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
    if item.is_deleted:
        raise GroundingError(
            f"{uid} is deleted — ratify the deletion to retire it, or `tl restore "
            f"{uid}` to bring it back (SR-0267)")
    idx = Index.build(project)
    set_status(schema, item, schema.status_role("invalidated"))
    item.attrs["invalidated_reason"] = reason or True
    done = cascade_suspicion(project, idx, uid, f"upstream {uid} invalidated")
    return Invalidation(done.affected, done.marked, done.refused)


def withdraw(project, uids, *, by: str, reason: str,
             by_id: str | None = None, of: str | None = None,
             at: str | None = None) -> list[Item]:
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

    ``of`` names whose signature is withdrawn, by identifier or by name (SR-0262):
    required where an item holds more than one, optional where it holds one. One
    entry goes and one withdrawal record is kept for it. The item moves to the
    suspect status only when the signatures left no longer meet what its kind
    requires; signatures that still do leave the status untouched.

    All or nothing: every item is checked before any is written, so a mistyped UID
    in a batch is a failure the caller sees, never a half-applied run. Refused for
    an item that carries no ratification, for an empty reason, for an ambiguous
    ``of``, and for a status the project's lifecycle will not let move to suspect
    where the move would be made.
    """
    if not by or not by.strip():
        raise GroundingError("a withdrawal must name who is withdrawing it")
    if not reason or not reason.strip():
        raise GroundingError("a withdrawal must state its reason — pass --reason")
    identifier = normalise_identifier(by_id)          # IdentityError if malformed
    of = (of or "").strip() or None
    schema = project.schema
    suspect = schema.status_role("suspect")
    plan: list[tuple[Item, dict, bool]] = []
    seen: set[str] = set()
    for uid in uids:
        if uid in seen:
            raise GroundingError(f"{uid} is named more than once")
        seen.add(uid)
        item = project.get(uid)
        if item is None:
            raise GroundingError(f"{uid} does not exist")
        # Of a tombstone, the signatures over its deletion (SR-0269): the ones
        # over its content are the history of what the deletion undid.
        held = relevant(item)
        if not held:
            raise GroundingError(f"{uid} carries no ratification to withdraw")
        if of is not None:
            matches = [e for e in held if e.get(ID) == of or e.get(BY) == of]
            if not matches:
                raise GroundingError(
                    f"{uid} carries no signature by {of} — it is signed by {names(item)}")
            if len(matches) > 1:
                raise GroundingError(
                    f"{uid}: '{of}' names {len(matches)} signatures — give the "
                    "signer's identifier instead")
            entry = matches[0]
        elif len(held) == 1:
            entry = held[0]
        else:
            raise GroundingError(
                f"{uid} carries {len(held)} signatures ({names(item)}) — say whose "
                "to withdraw with --of <name or identifier>")
        # Whether the item still counts as ratified without this signature. The
        # move to suspect follows the requirement, not the count: an item still
        # signed by the people its kind requires has lost nothing (SR-0262), and an
        # item that never met the requirement is not made suspect by losing a
        # signature it was still gathering.
        was = requirement_met(item, schema)
        everything = item.ratifications
        item.ratifications = [e for e in everything if e is not entry]
        still = requirement_met(item, schema)
        item.ratifications = everything
        # A tombstone moves nowhere (SR-0269): without the signature the deletion
        # is proposed again, and the status it holds already says so.
        moves = was and not still and not item.is_deleted
        if moves and not schema.allows_transition(item.status, suspect):
            raise GroundingError(
                f"{uid}: status change '{item.status}' -> '{suspect}' is not an "
                "allowed transition, so its ratification cannot be withdrawn")
        plan.append((item, entry, moves))
    items: list[Item] = []
    for item, entry, moves in plan:
        item.ratifications.remove(entry)
        # The withdrawn identity moves into the withdrawal record rather than
        # vanishing, so the graph still shows that a signature was taken and then
        # set aside — who withdrew it, why, whose it was and when (SR-0197, SR-0262).
        record: dict = {W_BY: by}
        if identifier is not None:
            record[W_ID] = identifier
        record[W_REASON] = reason
        record[W_OF] = entry.get(BY)
        if entry.get(ID):
            record[W_OF_ID] = entry[ID]
        record[W_AT] = at or now_iso()
        item.withdrawals.append(record)
        if moves:
            set_status(schema, item, suspect)
            item.attrs.setdefault("suspect_reasons", []).append(
                f"ratification by {entry.get(BY)} withdrawn by {by}: {reason}")
        items.append(item)
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
