# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Normative-content fingerprint (SR-0033, doc 06 §5).

SHA-256 over the fields whose change is a *real* requirement change: uid, type,
text, normative, derived, and any project attr the schema marks ``normative``.
Deliberately excludes order, title, status, links, reviewed, and timestamps so
that reordering and workflow changes never raise false suspects.

The set of normative attributes comes from the project :class:`Schema`
(``schema.normative_attrs``) — this module does not re-read the config itself.
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
        ("uid", item.uid),
        ("type", item.type),
        ("text", _norm(item.text)),
        ("normative", str(item.normative)),
        ("derived", str(item.derived)),
    ]
    names = schema.normative_attrs(item.type) if schema is not None else []
    for name in names:
        parts.append((f"attr:{name}", _norm(item.attrs.get(name, ""))))
    canonical = _REC.join(f"{k}{_UNIT}{v}" for k, v in parts)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
