# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""The links a ratification covers (SR-0242, SR-0243, SR-0244).

A project declares which link types a signature covers (``[ratify]
signed_links``). Ratify records the item's outgoing links of those types beside
the stamp, and a signature is stale when the two no longer agree.

The record is its own, not an input to the content fingerprint. That fingerprint
is also what a link records as its stamp of its target and what a review records,
so folding links into it would turn every link pointing at an item suspect
whenever that item changed its own links.

A link is its type and its target. Its stamp is left out: a restamp confirms the
target's content, which a suspect link already reports. A record that holds no
link record holds no links, so a signature made before the record existed is
stale exactly when the item carries a link of a signed type.
"""
from __future__ import annotations

from .schema import Schema

# The flat attribute of format 3 that held the signed links. Still named so the
# lift of an older record (SR-0255) and a merge reading an older file know it;
# a signature now holds its links under its own ``links`` key (SR-0254).
LINKS_ATTR = "ratified_links"

#: A signed link: ``(type, target)``.
SignedLink = tuple[str, str]


def signed_link_types(item, schema: Schema | None = None) -> frozenset[str]:
    """The link types a signature on ``item`` covers. The item's own graph
    decides, not the graph reading it (SR-0245), so only None falls through to
    the reading schema — an empty set is a real answer."""
    authored = item._authored_signed_link_types
    if authored is not None:
        return frozenset(authored)
    return schema.signed_link_types if schema is not None else frozenset()


def signed_links(item, schema: Schema | None = None) -> list[SignedLink]:
    """The links of signed types ``item`` carries, each once and sorted, with the
    target as the item's own file writes it (SR-0245)."""
    types = signed_link_types(item, schema)
    return sorted({(ln.type, ln.authored_target) for ln in item.links
                   if ln.type in types})


def links_record(item, schema: Schema | None = None) -> list[dict]:
    """What ratify writes under :data:`LINKS_ATTR` for ``item`` as it stands
    (SR-0243). Empty when it carries no signed link, and then nothing is written."""
    return [{"type": t, "target": target} for t, target in signed_links(item, schema)]


def recorded_links(entry: dict | None) -> list[SignedLink] | None:
    """The links one signature holds (its ``links`` key, SR-0254). A signature with
    no link record holds none (SR-0244). None when the record is not one the Tool
    could have written, which validation reports and nothing reads as agreement."""
    raw = entry.get("links") if entry else None
    if raw is None:
        return []
    if not isinstance(raw, list):
        return None
    out: list[SignedLink] = []
    for entry in raw:
        if (not isinstance(entry, dict) or set(entry) != {"type", "target"}
                or not all(isinstance(entry[k], str) and entry[k]
                           for k in ("type", "target"))):
            return None
        out.append((entry["type"], entry["target"]))
    return out


def link_drift(item, schema: Schema | None = None, entry: dict | None = None
               ) -> tuple[list[SignedLink], list[SignedLink]]:
    """``(unheld, dropped)`` for one signature on ``item`` (SR-0244, SR-0261): the
    signed links the item carries that the signature does not hold, and the links
    the signature holds that the item no longer carries. Links of types not signed
    are ignored on both sides. Both empty for a signature with no stamp, and for a
    malformed record, which is reported as its own error."""
    if not entry or not entry.get("fingerprint"):
        return [], []
    recorded = recorded_links(entry)
    if recorded is None:
        return [], []
    types = signed_link_types(item, schema)
    held = {ln for ln in recorded if ln[0] in types}
    carried = set(signed_links(item, schema))
    return sorted(carried - held), sorted(held - carried)


def links_moved(item, schema: Schema | None = None, entry: dict | None = None) -> bool:
    unheld, dropped = link_drift(item, schema, entry)
    return bool(unheld or dropped)


def describe_drift(unheld: list[SignedLink], dropped: list[SignedLink]) -> str:
    """The drift in words, naming each link and which of the two it is."""
    parts = []
    if unheld:
        parts.append("carries " + ", ".join(f"{t} → {target}" for t, target in unheld)
                     + ", which the signature does not cover")
    if dropped:
        parts.append("no longer carries " + ", ".join(
            f"{t} → {target}" for t, target in dropped) + ", which the signature covers")
    return "; ".join(parts)
