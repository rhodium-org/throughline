# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""The signatures an item carries, and whether they meet what its kind requires
(UR-0039, SR-0254, SR-0256, SR-0258, SR-0260, SR-0261).

An item's ratification record is a list, ``ratifications``, at the item's top
level: one entry per person who has signed (SR-0254). Each entry holds what the
flat record held before format 4 — the signer's name, their identifier, the stamp,
the recorded content, the recorded links, the identity a correction replaced and
the backfill marking — and, where known, the role signed in and the time (SR-0256).
Withdrawn signatures leave a record each in ``withdrawals`` (SR-0262).

Why a list inside the item file and not a file per signature: one file per item is
what lets a person or an agent read everything about an item in one place, and a
graph's file count is a cost its readers pay — Microsoft 365 Copilot reads fifty.
Why at the top level and not under ``attrs``: the record is core data the Tool
owns, not a project-declared attribute (SR-0022), and the guard that keeps every
other verb away from it (SR-0170, SR-0263) is simplest when the record is not where
attributes are set.

The flat record of format 3 is lifted into a list of one wherever an item is read
(:func:`lift_legacy`), so a graph at the earlier major, an item re-read from
history, and a file written by an older Tool all present the same shape. Nothing
is invented in the lift: a record that never held a role or a time has none.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .fingerprint import DELETED_KEY, deletion_content, deletion_fingerprint, fingerprint, signed_content
from .signed_links import links_moved

# The two top-level fields.
RATIFICATIONS = "ratifications"
WITHDRAWALS = "withdrawals"

# The keys of one signature.
BY = "by"
ID = "id"
ROLE = "role"
AT = "at"
STAMP = "fingerprint"
CONTENT = "content"
LINKS = "links"
SUPERSEDES = "supersedes"
BACKFILLED = "backfilled"
REVISION = "revision"
ENTRY_KEYS = (BY, ID, ROLE, AT, STAMP, CONTENT, LINKS, SUPERSEDES, BACKFILLED,
              REVISION)

# The keys of one withdrawal record: who withdrew it, their identifier, why,
# whose signature it was, that person's identifier, and when.
W_BY = "by"
W_ID = "id"
W_REASON = "reason"
W_OF = "of"
W_OF_ID = "of_id"
W_AT = "at"

# The flat attributes of format 3, and the entry key each becomes (SR-0255).
LEGACY_SIGNATURE = {
    "ratified_by": BY, "ratified_id": ID, "ratified_fingerprint": STAMP,
    "ratified_content": CONTENT, "ratified_links": LINKS,
    "ratified_supersedes": SUPERSEDES, "ratified_backfilled": BACKFILLED,
    "ratified_revision": REVISION,
}
LEGACY_WITHDRAWAL = {
    "withdrawn_by": W_BY, "withdrawn_id": W_ID, "withdrawn_reason": W_REASON,
    "withdrawn_ratifier": W_OF,
}


def lift_legacy(attrs: dict) -> tuple[list[dict], list[dict]]:
    """Take a format-3 flat record out of ``attrs`` and return it as
    ``([signature], [withdrawal])``, each list empty when the attributes held none.
    The attributes are removed from ``attrs``. Values are carried verbatim and
    nothing is added: no role and no time, since neither was recorded."""
    entry = {}
    for old, new in LEGACY_SIGNATURE.items():
        if old in attrs:
            entry[new] = attrs.pop(old)
    withdrawal = {}
    for old, new in LEGACY_WITHDRAWAL.items():
        if old in attrs:
            withdrawal[new] = attrs.pop(old)
    return ([entry] if entry else []), ([withdrawal] if withdrawal else [])


def now_iso() -> str:
    """The moment, as a signature records it (SR-0256): UTC, to the second."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def signatures(item) -> list[dict]:
    """The signatures ``item`` carries, oldest first: the entries that name who
    signed. An entry naming nobody is not a signature — it is a record something
    other than ratify left behind, which the gate reports (SR-0217) and the next
    ratification clears."""
    return [e for e in item.ratifications if e.get(BY)]


def signs_deletion(entry: dict) -> bool:
    """Whether ``entry`` is a signature over a deletion record (SR-0269) rather
    than over the item's content: its recorded content holds the record."""
    content = entry.get(CONTENT)
    return isinstance(content, dict) and DELETED_KEY in content


def relevant(item) -> list[dict]:
    """The signatures that bear on ``item`` as it stands (SR-0269): the deletion
    signatures of a tombstone, the content signatures of a live item. A tombstone
    keeps the signatures over its content as the history of what the deletion
    undid, and they count toward nothing; a live item cannot have been retired, so
    a deletion signature on one is a record something else left behind."""
    tombstone = item.is_deleted
    return [e for e in signatures(item) if signs_deletion(e) == tombstone]


def stamp_of(item, schema) -> str:
    """The fingerprint a signature over ``item`` as it stands is taken over: the
    deletion record of a tombstone (SR-0269), the content of anything else."""
    return deletion_fingerprint(item) if item.is_deleted else fingerprint(item, schema)


def signed_record(item, schema) -> dict:
    """What ratify records beside the stamp for ``item`` as it stands: the
    deletion record of a tombstone (SR-0269), the content of anything else
    (SR-0215)."""
    return deletion_content(item) if item.is_deleted else signed_content(item, schema)


def latest(item) -> dict | None:
    """The most recent signature that bears on the item as it stands, or None
    when nobody has signed it so."""
    held = relevant(item)
    return held[-1] if held else None


def same_person(entry: dict, by: str | None, by_id: str | None) -> bool:
    """Whether ``entry`` was signed by the person named. Identifiers decide where
    both sides have one (SR-0157); otherwise the name does."""
    if by_id and entry.get(ID):
        return entry[ID] == by_id
    return bool(by) and entry.get(BY) == by


def entry_of(item, *, by: str | None = None, by_id: str | None = None) -> dict | None:
    """The signature the person named gave over the item as it stands, or None.
    A person's signature over a tombstone's content is not their signature over
    its deletion (SR-0269)."""
    for entry in relevant(item):
        if same_person(entry, by, by_id):
            return entry
    return None


def names(item) -> str:
    """The signers' names as prose: ``A``, ``A and B``, ``A, B and C``."""
    seen: list[str] = []
    for entry in relevant(item):
        name = str(entry.get(BY) or "a human")
        if name not in seen:
            seen.append(name)
    return _prose(seen)


def _prose(parts: list[str]) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def describe(entry: dict) -> str:
    """One signature in words: the name, its identifier and role where held."""
    name = str(entry.get(BY) or "a human")
    bits = []
    if entry.get(ID):
        bits.append(str(entry[ID]))
    if entry.get(ROLE):
        bits.append(f"as {entry[ROLE]}")
    return name + (f" ({', '.join(bits)})" if bits else "")


def is_stale(item, schema, entry: dict) -> bool:
    """Whether ``entry`` no longer covers the item (SR-0261): its stamp differs
    from the fingerprint it was taken over, or the links it covers have moved. A
    signature with no stamp cannot be judged and is not called stale. A signature
    that does not bear on the item as it stands — a content signature kept on a
    tombstone as history (SR-0269) — is neither current nor stale, so it is not
    called stale either."""
    stamp = entry.get(STAMP)
    if not stamp or signs_deletion(entry) != item.is_deleted:
        return False
    if signs_deletion(entry):
        return stamp != deletion_fingerprint(item)
    return stamp != fingerprint(item, schema) or links_moved(item, schema, entry)


def stale_entries(item, schema) -> list[dict]:
    return [e for e in relevant(item) if is_stale(item, schema, e)]


def current(item, schema) -> list[dict]:
    """The signatures that still cover the item as it stands."""
    return [e for e in relevant(item) if not is_stale(item, schema, e)]


@dataclass(frozen=True)
class Standing:
    """Where an item stands against what its kind requires (SR-0260). ``held`` is
    the current signatures that count; ``missing`` says, in words, what is still
    required; ``met`` is whether nothing is."""
    held: tuple[dict, ...]
    missing: tuple[str, ...] = field(default_factory=tuple)

    @property
    def met(self) -> bool:
        return bool(self.held) and not self.missing

    def describe(self) -> str:
        have = ("signed by " + _prose([describe(e) for e in self.held])
                if self.held else "no signature stands")
        if not self.missing:
            return have
        return f"{have}; still requires {_prose(list(self.missing))}"


def standing(item, schema) -> Standing:
    """How ``item``'s current signatures measure against the requirement declared
    for its type. A project that declares no roles requires one signature from
    anyone, as it always has; one that declares roles counts only signatures given
    in a declared role, and may ask for particular roles and a least number."""
    requirement = schema.requirement(item.type)
    cur = current(item, schema)
    if not schema.roles:
        held = tuple(cur)
        missing = () if held else ("a signature",)
        return Standing(held=held, missing=missing)
    counted = tuple(e for e in cur if e.get(ROLE) in schema.roles)
    held_roles = {e.get(ROLE) for e in counted}
    missing: list[str] = []
    for role in requirement.roles:
        if role not in held_roles:
            missing.append(f"a signature in the role '{role}'")
    short = requirement.signatures - len(counted)
    if short > 0:
        if not missing or short > len(missing):
            missing.append(f"{short} more signature{'s' if short != 1 else ''} in a "
                           "declared role")
    return Standing(held=counted, missing=tuple(missing))


def requirement_met(item, schema) -> bool:
    """Whether ``item`` counts as ratified by its signatures alone (SR-0260)."""
    return standing(item, schema).met


# The key a tombstone's record carries when the item was retired outright
# (SR-0272): no signature stood on it or on any dependent, so none is undone and
# none is required.
OUTRIGHT = "outright"


def retired(item, schema) -> bool:
    """Whether ``item`` is a tombstone that stands (SR-0012, SR-0267): its deletion
    was ratified to the standing its kind requires, or it was retired outright
    (SR-0272). A tombstone nobody has signed is a proposed deletion — the item is
    still live scope, and nothing downstream has changed."""
    if not item.is_deleted:
        return False
    if (item.deleted or {}).get(OUTRIGHT):
        return True
    return requirement_met(item, schema)
