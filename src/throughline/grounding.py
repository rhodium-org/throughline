# Copyright (c) 2026 Time Back Solutions Limited
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

from .graph import Index
from .model import Item, Link


def reaches_root(idx: Index, schema, uid: str) -> bool:
    """True if ``uid`` grounds upward to a root type over the schema's grounding
    link types. ``schema`` is a :class:`throughline.schema.Schema`."""
    return idx.reaches(uid, schema.is_root, schema.ground_link_types)


# ------------------------------------------------------------------ operations

class GroundingError(ValueError):
    pass


def ratify(project, uid: str, by: str) -> Item:
    """A human takes accountability. Refused for ambiguous or ungrounded items —
    the two states that must not be signed off (scope-avalanche briefing §5)."""
    item = project.get(uid)
    if item is None:
        raise GroundingError(f"{uid} does not exist")
    schema = project.schema
    if item.attrs.get("ambiguous"):
        raise GroundingError(f"{uid} is flagged ambiguous and cannot be ratified until clarified")
    idx = Index.build(project)
    if not schema.is_root(item) and not reaches_root(idx, schema, uid):
        raise GroundingError(f"{uid} is not grounded to a root and cannot be ratified")
    item.status = "ratified"
    item.attrs["ratified_by"] = by
    return item


def invalidate(project, uid: str, reason: str = "") -> list[str]:
    """Falsify an assumption (or any node): retire it and mark every transitive
    dependent suspect. Returns the blast radius (SR-0035 reused)."""
    item = project.get(uid)
    if item is None:
        raise GroundingError(f"{uid} does not exist")
    idx = Index.build(project)
    affected = idx.impact(uid)          # incoming grounds_in + assumes edges
    item.status = "rejected"
    item.attrs["invalidated_reason"] = reason or True
    for aid in affected:
        dep = project.get(aid)
        if dep and dep.status not in ("rejected", "deleted"):
            dep.status = "suspect"
            reasons = dep.attrs.setdefault("suspect_reasons", [])
            reasons.append(f"upstream {uid} invalidated")
    return affected


def scout_ingest(project, report: dict) -> dict:
    """Fold a scout report in. Scout PROPOSES; humans RATIFY.

    report = {proposed_roots:[{id,type,title,rationale}],
              ambiguities:[{id,reason}], coverage_gaps:[{root,detail}]}
    Returns a summary and the Documents that changed (for write-back).
    """
    summary = {"roots_proposed": [], "ambiguities_flagged": [], "gaps": [], "touched": set()}

    # proposed roots need a home document; use the first doc whose prefix looks
    # right, else the first document in the project.
    for r in report.get("proposed_roots", []):
        if project.get(r["id"]) is not None:
            continue
        doc = project.document_of(r["id"]) or next(iter(project.documents.values()), None)
        if doc is None:
            continue
        item = Item(uid=r["id"], type=r.get("type", "business_need"),
                    status="proposed", title=r.get("title", ""),
                    text=r.get("rationale", ""))
        item.attrs["origin"] = "scout"
        item._doc_prefix = doc.prefix
        doc.items[item.uid] = item
        summary["roots_proposed"].append(item.uid)
        summary["touched"].add((doc.prefix, item.uid))

    for a in report.get("ambiguities", []):
        item = project.get(a["id"])
        if not item:
            continue
        item.attrs["ambiguous"] = True
        item.attrs.setdefault("suspect_reasons", []).append(f"scout: {a['reason']}")
        if item.status == "ratified":
            item.status = "suspect"
        summary["ambiguities_flagged"].append(item.uid)
        summary["touched"].add((item._doc_prefix, item.uid))

    for gap in report.get("coverage_gaps", []):
        summary["gaps"].append((gap.get("root"), gap.get("detail")))

    return summary


def link(item: Item, target: str, kind: str) -> None:
    item.links.append(Link(target=target, type=kind))
