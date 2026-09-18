# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Creating and changing an item — the operations, without a command line.

These are the functions `tl new` and `tl amend` are made of. They live here rather
than in :mod:`throughline.cli` so that any program embedding the Tool reaches them
by name instead of importing a command line (SR-0224, SR-0225): a composing tool
already imported two of them privately, and a browser runtime has no command line
to import at all.

Like the other operations, they change the item they are given and return what
happened; writing it back is the caller's (SR-0072).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .fingerprint import fingerprint
from .grounding import (
    AMBIGUOUS_ATTR,
    GroundingError,
    set_status,
    ambiguity_change_refusal,
    attribute_owner,
    attribute_removal_refusal,
    origin_change_refusal,
)
from .model import Item, Link
from .uid import UidError, next_uid, parse_uid

def coerce_attr(schema, item_type: str, key: str, raw: str):
    """Coerce a ``--attr KEY=VALUE`` string to the kind the schema declares for
    the attribute (SR-0142). An undeclared attribute is stored verbatim as a
    string; a declared int/float/bool is converted so it round-trips as the right
    YAML scalar rather than a quoted string, and a declared enum is checked for
    membership (SR-0023). A value the schema cannot accept is a hard error at
    creation (fail-fast), not a surprise the loader raises later."""
    spec = schema.attr(item_type, key)
    # An undeclared attribute is stored as the text given, which made
    # `--attr ambiguous=false` a non-empty string and so a flag: asking for no flag
    # raised one. The Tool reads this attribute as a flag everywhere, so it reads
    # the value as one here (SR-0223). A project that declares it keeps its kind.
    default_kind = "bool" if key == AMBIGUOUS_ATTR else "string"
    kind = spec.kind if spec is not None else default_kind
    try:
        if kind == "enum":
            if raw not in spec.values:
                raise ValueError(f"not in {list(spec.values)}")
            return raw
        if kind == "int":
            return int(raw)
        if kind == "float":
            return float(raw)
        if kind == "bool":
            low = raw.strip().lower()
            if low in ("true", "1", "yes"):
                return True
            if low in ("false", "0", "no"):
                return False
            raise ValueError(f"expected a boolean, got '{raw}'")
    except ValueError as e:
        raise UidError(f"--attr {key}={raw}: {e}") from e
    return raw


def parse_attrs(schema, item_type: str, pairs: list[str] | None,
                 *, command: str, declared_only: bool = False) -> dict:
    """Parse repeated ``--attr KEY=VALUE`` options into a coerced attrs dict.

    ``command`` names the verb doing the setting, so a refusal can say which command
    owns an attribute it will not write. ``declared_only`` rejects an attribute the
    item's type does not declare instead of storing it verbatim — what `amend`
    requires (SR-0144) and what creation deliberately does not, since an attribute
    an evolving schema has not caught up with is a reasonable thing to author."""
    attrs: dict = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise UidError(f"--attr expects KEY=VALUE, got '{pair}'")
        key, raw = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise UidError(f"--attr expects a non-empty key, got '{pair}'")
        # The ratification record is evidence that a named person took
        # accountability, and evidence is worth what it costs to forge. No verb but
        # the one that owns it may write it (SR-0170), and the record of a removed
        # ambiguity flag is guarded the same way (SR-0213).
        owned = attribute_owner(key)
        if owned is not None:
            record, owner = owned
            raise UidError(
                f"--attr {key}: '{key}' is part of the {record} and "
                f"cannot be set by `tl {command}` — `tl {owner}` owns it")
        if declared_only and schema.attr(item_type, key) is None:
            raise UidError(
                f"--attr {key}: '{item_type}' declares no attribute '{key}'")
        attrs[key] = coerce_attr(schema, item_type, key, raw)
    return attrs


def birth_item(schema, reg, uid: str, *, item_type: str, title: str = "",
               text: str = "", status: str | None = None,
               origin: str | None = None, attrs: dict | None = None):
    """Everything an item receives at birth, in one place (SR-0205), offered to
    any tool that creates items in a project so `tl new` and a composing tool
    bear an item identically. ``attrs`` are the author's already-parsed values
    (see :func:`_parse_attrs`); ``status`` overrides the birth status explicitly.
    The caller adds grounding links and writes the item."""
    attrs = dict(attrs or {})
    # --origin is the canonical way to set provenance, but honour origin given via
    # --attr too so birth status stays consistent with what actually lands on the
    # item.
    origin = origin or attrs.get("origin")
    # Birth status comes from the project's status roles, never a value fixed in
    # code (SR-0131); ``status`` overrides it explicitly. A machine-origin item is
    # born 'proposed' — not 'initial' — so the ratification gate (SR-0092)
    # actually engages and a named human must ratify it before it counts; without
    # this a machine-authored item would enter the ordinary initial status and
    # silently escape the gate the tool exists to enforce (SR-0141). If the
    # project declares no 'proposed' role we fall back to 'initial'.
    has_proposed_role = bool((schema.status_roles or {}).get("proposed"))
    if status is None:
        if origin in schema.ai_origins and has_proposed_role:
            status = schema.status_role("proposed")
        else:
            status = schema.status_role("initial")
    # The flag is the kind's, not the command's (SR-0201): an intent or a
    # non-goal is born non-normative because its type says so.
    item = Item(uid=uid, type=item_type, status=status, title=title, text=text,
                normative=schema.is_normative(item_type))
    item.attrs.update(attrs)
    if origin:
        item.attrs["origin"] = origin
    # Apply schema-declared attribute defaults (SR-0138): a default only ever
    # lands at birth on an attribute the author did not set, so a schema sentinel
    # (e.g. a priority meaning "no human has decided yet") appears automatically
    # without overwriting an explicit value.
    for name, spec in schema.attrs_for(item_type).items():
        # Never a record a single verb owns, whatever a hand-edited config
        # declares: only that verb writes it (SR-0170, SR-0213, SR-0219), and
        # `tl schema attr add` refuses to declare one.
        if attribute_owner(name) is not None:
            continue
        if spec.default is not None and name not in item.attrs:
            item.attrs[name] = spec.default
    item._register_prefix = reg.prefix
    return item



@dataclass
class Amendment:
    """What an amendment did, so a caller can report it without recomputing it
    (SR-0169). ``changed`` names each field or attribute that moved, ``suspects``
    the dependents whose confirmed link this change has just invalidated."""

    uid: str
    changed: list[str] = field(default_factory=list)
    suspects: list[str] = field(default_factory=list)
    review_cleared: bool = False
    fingerprint_before: str = ""
    fingerprint_after: str = ""
    ratifier: str | None = None
    ratification_stale: bool = False

    @property
    def normative_change(self) -> bool:
        return self.fingerprint_before != self.fingerprint_after


def newly_suspect(project, uid: str, was: str, now: str) -> list[str]:
    """Dependents whose confirmed link to ``uid`` this content change has just
    invalidated (SR-0034, SR-0169).

    A link carries the target's fingerprint as at the last confirmation, so what
    makes a dependent *newly* suspect is a stamp that matched the old content and
    does not match the new. A stamp that already disagreed was suspect before this
    change and is not this change's doing; an unstamped link was never confirmed
    and so has nothing to lose."""
    if was == now:
        return []
    out = []
    for it in project.items():
        if it.is_deleted:
            continue
        if any(l.target == uid and l.stamp == was for l in it.links):
            out.append(it.uid)
    return sorted(out)


def amend_item(project, uid: str, *, title: str | None = None,
               text: str | None = None, rationale: str | None = None,
               attrs: dict | None = None, unset=()) -> Amendment:
    """Change an item's content in place and report what it cost (SR-0144, SR-0169).

    ``attrs`` are already-coerced values (see :func:`parse_attrs`); ``unset`` names
    attributes to remove. Every refusal is decided before anything moves, so a
    refused amendment changes nothing: an origin leaves the machine-origin set only
    by ratification (SR-0208), the ambiguity flag leaves an item only by
    clarification (SR-0213), and removal stops at the attributes a gate reads
    (SR-0206). The item is changed and returned; writing it back is the caller's.
    """
    item = project.get(uid)
    if item is None:
        raise GroundingError(f"{uid} does not exist")
    if item.is_deleted:
        raise GroundingError(f"{uid} is deleted — a tombstone is permanent (SR-0093)")
    attrs = dict(attrs or {})
    unset = list(dict.fromkeys(name.strip() for name in unset))
    if "" in unset:
        raise GroundingError("an attribute to remove must be named")
    if title is None and text is None and rationale is None and not attrs and not unset:
        raise GroundingError(f"{uid}: nothing to amend — give a title, text, "
                             "rationale, an attribute to set, or one to remove")
    both = sorted(set(unset) & set(attrs))
    if both:
        raise GroundingError(f"{', '.join(both)} would be both set and removed — "
                             "say which you mean")
    schema = project.schema
    if "origin" in attrs:
        refusal = origin_change_refusal(schema, item, attrs["origin"])
        if refusal is not None:
            raise GroundingError(refusal)
    if "ambiguous" in attrs:
        refusal = ambiguity_change_refusal(item, attrs["ambiguous"])
        if refusal is not None:
            raise GroundingError(refusal)
    for name in unset:
        refusal = attribute_removal_refusal(schema, item, name)
        if refusal is not None:
            raise GroundingError(refusal)

    before = fingerprint(item, schema)
    was_reviewed = item.reviewed is not None
    changed: list[str] = []
    # None means "not given"; an empty string is a real value that clears the
    # field, which is the only way to withdraw a rationale without opening the YAML.
    if title is not None and title != item.title:
        item.title = title
        changed.append("title")
    if text is not None and text != item.text:
        item.text = text
        changed.append("text")
    if rationale is not None and rationale != item.rationale:
        item.rationale = rationale
        changed.append("rationale")
    for key, value in attrs.items():
        if item.attrs.get(key) != value:
            item.attrs[key] = value
            changed.append(key)
    # Removal reaches an attribute the type no longer declares (SR-0206), which is
    # what a withdrawn attribute leaves behind and what nothing could clear before.
    for name in unset:
        del item.attrs[name]
        changed.append(f"{name} (unset)")
    now = fingerprint(item, schema)
    # A review confirms content, so content that has moved is no longer confirmed
    # (SR-0038, SR-0144). Only a normative change can invalidate it — retitling
    # leaves the fingerprint alone, and clearing a review it did not disturb would
    # cost the author a re-review for nothing.
    review_cleared = bool(was_reviewed and now != before and changed)
    if review_cleared:
        item.reviewed = None
    stamp = item.attrs.get("ratified_fingerprint")
    return Amendment(
        uid=uid, changed=changed,
        suspects=newly_suspect(project, uid, before, now) if changed else [],
        review_cleared=review_cleared,
        fingerprint_before=before, fingerprint_after=now,
        ratifier=item.attrs.get("ratified_by"),
        ratification_stale=bool(stamp and stamp != now),
    )


def delete_item(project, uid: str, *, reason: str = "unspecified") -> bool:
    """Tombstone ``uid``: the file stays, the item stops counting, and the UID is
    never reused (SR-0012, SR-0093). True when this call retired it, False when it
    was already retired.

    A tombstone is permanent, and so is what it records, so an item already retired
    is left exactly as it stands rather than gaining a second date and reason over
    the first. The item is changed and returned to the caller to write."""
    item = project.get(uid)
    if item is None:
        raise GroundingError(f"{uid} does not exist")
    schema = project.schema
    tombstone = schema.status_role("tombstone")
    if item.status == tombstone:
        return False
    set_status(schema, item, tombstone)
    # What the tombstone keeps (SR-0012): the day the UID was retired, why, and the
    # fingerprint of the content it last held, so the record says what was retired
    # wherever the file travels without its history. The day is taken in UTC so it
    # does not depend on where the command was run.
    item.deleted = {
        "date": datetime.now(timezone.utc).date().isoformat(),
        "reason": reason or "unspecified",
        "fingerprint": fingerprint(item, schema),
    }
    return True


def review_items(project, uids=None, *, all_items: bool = False) -> list:
    """Mark items reviewed at their current content (SR-0038), returning the ones
    whose review record moved so the caller writes those and no others.

    A review confirms content, so re-confirming content already confirmed writes
    nothing. A deleted item is dead scope and is passed over."""
    if all_items:
        targets = list(project.items())
    else:
        targets = []
        for uid in uids or ():
            item = project.get(uid)
            if item is None:
                raise GroundingError(f"{uid} does not exist")
            targets.append(item)
    moved = []
    for item in targets:
        if item is None or item.is_deleted:
            continue
        fp = fingerprint(item, project.schema)
        if item.reviewed != fp:
            item.reviewed = fp
            moved.append(item)
    return moved


def new_item(project, prefix: str, *, item_type: str, uid: str | None = None,
             title: str = "", text: str = "", status: str | None = None,
             origin: str | None = None, attrs: dict | None = None,
             ground=(), ground_type: str = "derives_from") -> Item:
    """Create an item in ``prefix``'s register, grounded at birth (SR-0005, SR-0073).

    The UID is allocated from the register unless one is given, in which case it
    must match the prefix and be unused. Every grounding target named is attached,
    including for a root type, which may legitimately carry one: an explicitly
    requested link is authoring intent and is never silently dropped (SR-0091). A
    target that does not exist is a refusal, not a link to nothing.

    The item is returned for the caller to write, along with its register, which is
    where `tl new` also asks a human for a parent when none was named.
    """
    reg = project.registers.get(prefix)
    if reg is None:
        raise GroundingError(
            f"no register with prefix '{prefix}' — create one before adding items")
    if uid is not None:
        pfx, _number = parse_uid(uid)               # UidError if malformed
        if pfx != prefix:
            raise UidError(f"{uid} does not match prefix {prefix}")
        if project.get(uid) is not None:
            raise GroundingError(f"{uid} already exists")
    else:
        uid = next_uid(reg)
    item = birth_item(project.schema, reg, uid, item_type=item_type, title=title,
                      text=text, status=status, origin=origin, attrs=attrs)
    for target in ground:
        if project.get(target) is None:
            raise GroundingError(f"grounding target {target} does not exist")
        item.links.append(Link(target=target, type=ground_type))
    reg.items[uid] = item
    return item
