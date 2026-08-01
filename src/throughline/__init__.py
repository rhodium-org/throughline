# Copyright (c) 2026 Henry J Grech-Cini
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
from .model import Item, Link, Project, Register
from .schema import AttrSpec, LinkRule, Schema, SchemaError
from .storage import (
    ProjectError,
    init_project,
    load_project,
    read_project,
    write_item,
    write_manifest,
)
from .uid import UID_RE, collisions, format_uid, next_uid, parse_uid
from .validate import Finding, is_external, is_namespace_qualified, validate
from .version import distribution_version, is_editable

# Read from the installed distribution, never restated here (SR-0164). Held as a
# literal it is a second copy of a fact that already lives in pyproject.toml, and
# the two drift in silence: 1.9.0 shipped reporting "1.8.0" because the release
# bumped one and not the other, and nothing failed — the wrong answer was simply
# returned to whoever asked. An editable install is marked as such, because a clean
# release number for a working tree is the same wrong answer in a quieter form.
__version__ = distribution_version("throughline")

__all__ = [
    "Register", "Item", "Link", "Project",
    "load_project", "read_project", "init_project", "write_item", "write_manifest",
    "ProjectError",
    "Index", "fingerprint",
    "UID_RE", "parse_uid", "format_uid", "next_uid", "collisions",
    "validate", "Finding", "is_external", "is_namespace_qualified",
    "Schema", "AttrSpec", "LinkRule", "SchemaError",
    "GroundingError", "reaches_root", "ratify", "invalidate",
    "distribution_version", "is_editable",
    "__version__",
]
