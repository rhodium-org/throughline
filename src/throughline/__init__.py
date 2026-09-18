# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""throughline — a Git-native requirements management tool with a grounding layer.

Public surface: the pure model, the storage layer, the link-graph index, the
fingerprint, the validation pipeline, and the grounding operations. The ``tl``
CLI (``throughline.cli``) is the primary entry point.
"""
from __future__ import annotations

from . import schema_ops
from .brief import context_item_section, context_markdown
from .diagrams import diagram_transitions, diagram_types
from .dump import build_dump
from .fingerprint import fingerprint
from .graph import Index
from .grounding import (
    GroundingError,
    Invalidation,
    Refusal,
    ambiguity_report,
    attribute_owner,
    clarify,
    flag,
    grounding_gap,
    invalidate,
    is_flagged_ambiguous,
    is_unserved,
    ratification_obstacle,
    ratify,
    reaches_root,
    set_status,
    transition_refusal,
    withdraw,
)
from .identity import (
    IdentityError,
    default_ratifier,
    git_identity,
    normalise_identifier,
)
from .inject import (
    DocumentRender,
    InjectError,
    TargetResolver,
    document_paths,
    has_markers,
    inject_documents,
    inject_text,
    referenced_uids,
    register_directive,
    render_item,
)
from .items import (
    Amendment,
    amend_item,
    birth_item,
    coerce_attr,
    delete_item,
    new_item,
    newly_suspect,
    parse_attrs,
    review_items,
)
from .links import LinkError, add_link, remove_link, retype_link
from .model import Item, Link, Project, Register
from .ratification import (
    ADDED,
    CHANGED,
    HISTORY,
    KEPT,
    RECORD,
    REMOVED,
    UNCHANGED,
    UNRATIFIED,
    UNRESOLVABLE,
    DiffUnit,
    FieldChange,
    RatificationChange,
    change_since_ratification,
    diff_prose,
    is_prose,
    ratification_is_committed,
    wrap_words,
)
from .schema import AttrSpec, LinkRule, Schema, SchemaError
from .views import (
    check_summary,
    ground_line,
    render_for_ratification,
    render_subgraph,
    render_trace,
    subgraph_json,
)
from .worklist import (
    CONCERNS,
    WorklistEntry,
    depths_from_roots,
    entry_for,
    is_ratified,
    ratification_progress,
    signature_is_stale,
    worklist,
)
from .storage import (
    CONFIG_NAME,
    MANIFEST_NAME,
    ProjectError,
    create_register,
    init_project,
    load_project,
    load_project_at_ref,
    migrate_project,
    read_project,
    write_item,
    write_manifest,
)
from .uid import UID_RE, collisions, format_uid, next_uid, parse_uid
from .validate import (
    Finding,
    FilterError,
    eval_filter,
    is_external,
    is_namespace_qualified,
    query_items,
    validate,
)
from .version import distribution_version, is_editable

# Read from the installed distribution, never restated here (SR-0164). Held as a
# literal it is a second copy of a fact that already lives in pyproject.toml, and
# the two drift in silence: 1.9.0 shipped reporting "1.8.0" because the release
# bumped one and not the other, and nothing failed — the wrong answer was simply
# returned to whoever asked. An editable install is marked as such, because a clean
# release number for a working tree is the same wrong answer in a quieter form.
__version__ = distribution_version("throughline")

# What this package offers a program embedding the Tool (SR-0224). It is the
# compatibility surface NFR-0011 names, listed in docs/referenced-resource/
# 10_library_api.md, and a test fails when the two disagree (SR-0228).
__all__ = [
    # the model and its storage
    "Register", "Item", "Link", "Project",
    "load_project", "read_project", "load_project_at_ref", "init_project",
    "migrate_project", "write_item", "write_manifest", "create_register",
    "ProjectError", "CONFIG_NAME", "MANIFEST_NAME",
    # identity of an item
    "UID_RE", "parse_uid", "format_uid", "next_uid", "collisions",
    "Index", "fingerprint",
    # the schema a project declares
    "Schema", "AttrSpec", "LinkRule", "SchemaError",
    # creating and changing an item
    "birth_item", "parse_attrs", "coerce_attr", "amend_item", "Amendment",
    "newly_suspect", "new_item", "delete_item", "review_items",
    "add_link", "remove_link", "retype_link", "LinkError",
    # the gate
    "validate", "Finding", "is_external", "is_namespace_qualified",
    "query_items", "eval_filter", "FilterError",
    # the grounding layer
    "GroundingError", "reaches_root", "grounding_gap", "is_unserved",
    "set_status", "transition_refusal", "Refusal", "Invalidation",
    "ratify", "ratification_obstacle", "invalidate", "withdraw",
    "flag", "clarify", "is_flagged_ambiguous", "ambiguity_report",
    "attribute_owner",
    # what a signature covered, and what has moved since
    "change_since_ratification", "ratification_is_committed",
    "RatificationChange", "FieldChange",
    "CHANGED", "UNCHANGED", "UNRESOLVABLE", "UNRATIFIED", "RECORD", "HISTORY",
    "is_prose", "diff_prose", "DiffUnit", "KEPT", "REMOVED", "ADDED", "wrap_words",
    # what awaits a signature, for an interface to draw
    "worklist", "WorklistEntry", "entry_for", "CONCERNS", "depths_from_roots",
    "is_ratified", "signature_is_stale", "ratification_progress",
    # who signs
    "default_ratifier", "git_identity", "normalise_identifier", "IdentityError",
    # publishing
    "inject_text", "inject_documents", "document_paths", "DocumentRender",
    "referenced_uids", "has_markers", "render_item", "register_directive",
    "TargetResolver", "InjectError", "build_dump",
    "diagram_types", "diagram_transitions", "schema_ops",
    # showing an item, its neighbourhood, and the project itself
    "context_markdown", "context_item_section", "check_summary",
    "render_trace", "render_subgraph",
    "subgraph_json", "render_for_ratification", "ground_line",
    # the running build
    "distribution_version", "is_editable",
    "__version__",
]
