# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Normative-content fingerprint (SR-0033, doc 06 §5).

SHA-256 over the fields whose change is a *real* requirement change: uid, type,
text, normative, derived, and any project attr the schema marks ``normative``.
Deliberately excludes order, title, status, links, reviewed, and timestamps so
that reordering and workflow changes never raise false suspects.

The set of normative attributes comes from the project :class:`Schema`
(``schema.normative_attrs``) — this module does not re-read the config itself.

Both inputs are taken as the item's *authoring* graph gave them, not as the graph
now reading it declares them. The uid is the one the item was authored under
(SR-0154), and the normative attribute names are the ones its own graph marked
(SR-0162). A tool that composes several graphs must re-label borrowed items to
keep identity unique in the merged graph, and validates the merged graph under a
single schema — the consumer's. Both of those are the consumer's choices, made
for the consumer's reasons; were either to reach the fingerprint, every stamp
written in a source graph would read as drifted in every consumer of that source,
on content nobody had touched.
"""
from __future__ import annotations

import copy
import hashlib
import unicodedata

from .schema import Schema

_UNIT = "\x1f"    # field key/value separator
_REC = "\x1e"     # field record separator


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFC", str(value))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def normative_attr_names(item, schema: Schema | None = None) -> list[str]:
    """The attributes the fingerprint reads for ``item``, in the order it reads them.

    Which attributes count is the authoring graph's judgement, not the reading
    graph's (SR-0162). A union is governed by the consumer's schema, so without
    this the set of attributes hashed would change the moment an item was
    borrowed and every stamp written in a source graph would read as drifted on
    content nobody had touched — the hazard SR-0154 closed for the UID, in the
    other half of the input. An empty tuple is a real answer ("that graph marked
    none"), so only None falls through to the reading schema."""
    authored = item._authored_normative_attrs
    if authored is not None:
        return list(authored)
    return schema.normative_attrs(item.type) if schema is not None else []


def _digest(uid, type_, text, normative, derived, attrs) -> str:
    parts = [
        ("uid", uid),
        ("type", type_),
        ("text", _norm(text)),
        ("normative", str(normative)),
        ("derived", str(derived)),
    ]
    for name, value in attrs:
        parts.append((f"attr:{name}", _norm(value)))
    canonical = _REC.join(f"{k}{_UNIT}{v}" for k, v in parts)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def fingerprint(item, schema: Schema | None = None) -> str:
    names = normative_attr_names(item, schema)
    return _digest(item.authored_uid, item.type, item.text, item.normative,
                   item.derived, [(name, item.attrs.get(name, "")) for name in names])


# The keys of a recorded content map (SR-0215): every input the fingerprint reads
# except the UID, which an item never changes. Held as values, not a digest, so the
# words a person agreed to can be shown without version control.
CONTENT_KEYS = ("type", "text", "normative", "derived", "attrs")


def signed_content(item, schema: Schema | None = None) -> dict:
    """The content a ratification stamp is taken over, as the item holds it now
    (SR-0215). With the item's UID it reproduces :func:`fingerprint` exactly: the
    attributes are the ones the fingerprint reads, in its order, each holding the
    value the fingerprint reads — an absent one as the empty string it hashes.

    Values are copied, never shared with the item. A list or a date held by both
    the item and its record is written once with a YAML anchor and read back as one
    object, so an edit to the item would silently rewrite what was signed."""
    return copy.deepcopy({
        "type": item.type,
        "text": item.text,
        "normative": item.normative,
        "derived": item.derived,
        "attrs": {name: item.attrs.get(name, "")
                  for name in normative_attr_names(item, schema)},
    })


def content_fingerprint(uid: str, content: object) -> str | None:
    """The fingerprint a recorded content map reproduces for ``uid`` (SR-0216,
    SR-0217), or None when the map is not one the Tool could have written — a
    hand edit that dropped a key or replaced the map with something else. None is
    never equal to a stamp, so a malformed record can only read as not reproducing
    it.

    Its values are read exactly as :func:`fingerprint` reads an item's, which hashes
    each as text: whatever the fingerprint accepted when the stamp was written, the
    record reproduces, so the Tool never writes a record its own check rejects."""
    # Every key this version reads must be there; a later version may record more
    # beside them, and those do not change what this one hashes.
    if not isinstance(content, dict) or not set(CONTENT_KEYS) <= set(content):
        return None
    attrs = content["attrs"]
    if not isinstance(attrs, dict) or not all(isinstance(k, str) for k in attrs):
        return None
    return _digest(uid, content["type"], content["text"], content["normative"],
                   content["derived"], list(attrs.items()))
