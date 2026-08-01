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

import hashlib
import unicodedata

from .schema import Schema

_UNIT = "\x1f"    # field key/value separator
_REC = "\x1e"     # field record separator


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFC", str(value))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def fingerprint(item, schema: Schema | None = None) -> str:
    parts = [
        ("uid", item.authored_uid),
        ("type", item.type),
        ("text", _norm(item.text)),
        ("normative", str(item.normative)),
        ("derived", str(item.derived)),
    ]
    # Which attributes count is the authoring graph's judgement, not the reading
    # graph's (SR-0162). A union is governed by the consumer's schema, so without
    # this the set of attributes hashed would change the moment an item was
    # borrowed and every stamp written in a source graph would read as drifted on
    # content nobody had touched — the hazard SR-0154 closed for the UID, in the
    # other half of the input. An empty tuple is a real answer ("that graph marked
    # none"), so only None falls through to the reading schema.
    authored = item._authored_normative_attrs
    if authored is not None:
        names = list(authored)
    else:
        names = schema.normative_attrs(item.type) if schema is not None else []
    for name in names:
        parts.append((f"attr:{name}", _norm(item.attrs.get(name, ""))))
    canonical = _REC.join(f"{k}{_UNIT}{v}" for k, v in parts)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
