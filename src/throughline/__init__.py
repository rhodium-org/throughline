# Copyright (c) 2026 Time Back Solutions Limited
# SPDX-License-Identifier: Apache-2.0
"""throughline — a Git-native requirements management tool with a grounding layer.

Public surface: the pure model, the storage layer, the link-graph index, the
fingerprint, the validation pipeline, and the grounding operations. The ``tl``
CLI (``throughline.cli``) is the primary entry point.
"""
from __future__ import annotations

from .fingerprint import fingerprint
from .graph import Index
from .grounding import (
    GroundingError,
    invalidate,
    ratify,
    reaches_root,
)
from .model import Document, Item, Link, Project
from .schema import AttrSpec, LinkRule, Schema, SchemaError
from .storage import ProjectError, init_project, load_project, write_item, write_manifest
from .uid import UID_RE, collisions, format_uid, next_uid, parse_uid
from .validate import Finding, validate

__version__ = "0.1.3"

__all__ = [
    "Document", "Item", "Link", "Project",
    "load_project", "init_project", "write_item", "write_manifest", "ProjectError",
    "Index", "fingerprint",
    "UID_RE", "parse_uid", "format_uid", "next_uid", "collisions",
    "validate", "Finding",
    "Schema", "AttrSpec", "LinkRule", "SchemaError",
    "GroundingError", "reaches_root", "ratify", "invalidate",
    "__version__",
]
