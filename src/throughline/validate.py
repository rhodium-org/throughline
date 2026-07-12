# Copyright (c) 2026 Time Back Solutions Limited
# SPDX-License-Identifier: Apache-2.0
"""Validation pipeline (SR-0040..44). Each rule yields Findings
{rule, severity, uid, file, message}; `check` aggregates and sets the exit code.

Rule severities are configurable (SR-0041) via [rules] / [rules.<name>] in
throughline.toml; --strict promotes every warning to an error for CI.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .fingerprint import fingerprint
from .graph import Index
from .grounding import reaches_root
from .schema import ERROR, OFF, WARNING
from .uid import UID_RE, collisions


@dataclass
class Finding:
    rule: str
    severity: str
    uid: str
    file: str
    message: str

    def to_dict(self) -> dict:
        return {"rule": self.rule, "severity": self.severity, "uid": self.uid,
                "file": self.file, "message": self.message}

    def __str__(self) -> str:
        tag = "ERROR" if self.severity == ERROR else "warn "
        return f"[{tag}] {self.uid:<12} {self.rule:<16} {self.message}"


# severities a project may override under [rules] (rule_name = "error"|"warning"|"off")
_DEFAULT_SEVERITY = {
    "uid-grammar": ERROR, "uid-collision": ERROR, "schema": ERROR,
    "dangling-link": ERROR, "deleted-link-target": ERROR, "refines-cycle": ERROR,
    "grounding-cycle": ERROR, "orphan": ERROR, "unserved-root": ERROR,
    "bad-link-target": ERROR, "bad-status": ERROR, "bad-transition": ERROR,
    "bad-link-shape": ERROR, "tombstone-deleted": ERROR,
    "suspect-link": WARNING, "unreviewed": WARNING, "unratified": WARNING,
    "ambiguous": WARNING, "coverage": WARNING, "vague-word": WARNING,
    "unpublished": WARNING,
}


def _file(item) -> str:
    return str(item._path) if item._path else ""


def validate(project, strict: bool = False,
             baseline: dict[str, str] | None = None,
             published: set[str] | None = None) -> list[Finding]:
    idx = Index.build(project)
    schema = project.schema
    out: list[Finding] = []

    def add(rule, uid, file, msg):
        sev = schema.rule_severity(rule, _DEFAULT_SEVERITY.get(rule, WARNING), strict)
        if sev != OFF:
            out.append(Finding(rule, sev, uid, file, msg))

    # UID collisions across the whole project (SR-0006).
    for uid in collisions(project):
        add("uid-collision", uid, "", "UID appears in more than one item (merge collision)")

    # A tombstone is permanent (SR-0093): an item that was `deleted` at the
    # baseline but is now absent from the working tree means the record of a
    # retired UID has been erased — a bad merge or a stray `git rm`. The vanished
    # item never appears in project.items(), so catch it here off the baseline.
    if baseline is not None:
        for uid, prev in baseline.items():
            if prev == "deleted" and project.get(uid) is None:
                add("tombstone-deleted", uid, "",
                    "tombstone erased: this UID was retired but its record is gone "
                    "(a UID's death record must never be removed)")

    ground_kinds = schema.ground_link_types
    cycle_types = ground_kinds | {"refines"}

    cyc = idx.refines_cycle(cycle_types)
    if cyc:
        add("grounding-cycle", cyc[0], _file(project.get(cyc[0]) or _dummy()),
            "circular justification: " + " -> ".join(cyc))

    for item in project.items():
        if item.is_deleted:
            continue
        f = _file(item)

        # UID grammar + filename agreement (SR-0002, doc 06 §11.1).
        if not UID_RE.match(item.uid):
            add("uid-grammar", item.uid, f, "UID does not match <PREFIX>-<NUMBER>")

        # Status membership against the declared vocabulary (SR-0081).
        if not schema.is_status(item.status):
            add("bad-status", item.uid, f,
                f"status '{item.status}' is not in the project's status set")

        # Status transition legality vs the baseline (SR-0083). A missing
        # baseline entry means the item is new here — creation is not a move.
        if baseline is not None:
            prev = baseline.get(item.uid)
            if (prev is not None and prev != item.status
                    and not schema.allows_transition(prev, item.status)):
                add("bad-transition", item.uid, f,
                    f"status change '{prev}' -> '{item.status}' is not an "
                    "allowed transition")

        # Schema: required attrs + enum membership (SR-0023).
        for name, spec in schema.attrs_for(item.type).items():
            present = name in item.attrs and item.attrs[name] not in (None, "")
            if spec.required and not present:
                add("schema", item.uid, f, f"required attribute '{name}' missing")
            if present and spec.kind == "enum" and item.attrs[name] not in spec.values:
                add("schema", item.uid, f,
                    f"attribute '{name}'='{item.attrs[name]}' not in {list(spec.values)}")

        # Link integrity (SR-0032) + type legality (SR-0030).
        for link in item.links:
            if not schema.is_link_type(link.type):
                add("bad-link-target", item.uid, f, f"unknown link type '{link.type}'")
            external = _is_external(link.target)
            target = None if external else project.get(link.target)
            # Endpoint-type shape vs [link_rules] (SR-0084). Target type is
            # unknown for external or missing targets, so only the source side
            # is checked there.
            dst_type = target.type if target is not None else None
            shape = schema.link_allowed(link.type, item.type, dst_type)
            if shape:
                add("bad-link-shape", item.uid, f, shape)
            if external:
                continue
            if target is None:
                add("dangling-link", item.uid, f, f"link target '{link.target}' does not exist")
            elif target.is_deleted:
                add("deleted-link-target", item.uid, f, f"link target '{link.target}' is deleted")

        # Grounding coverage — the anti-avalanche core (upward).
        if not schema.is_root(item):
            if not idx.out_links(item.uid, ground_kinds):
                add("orphan", item.uid, f,
                    f"{item.type} has no grounding link — nothing justifies it")
            elif not reaches_root(idx, schema, item.uid):
                add("orphan", item.uid, f,
                    "grounding chain never reaches a root")

        # Downward coverage — every delivery root must be served (mirror).
        if item.type in schema.delivery_roots and not idx.in_links(item.uid, ground_kinds):
            add("unserved-root", item.uid, f,
                f"{item.type} has nothing deriving from / mitigating it — unserved")

        # Provenance — generated volume must not masquerade as ratified (SR-0020 attr).
        if item.attrs.get("origin") in schema.ai_origins and item.status == "proposed":
            add("unratified", item.uid, f,
                f"{item.attrs['origin']}-origin item awaiting human ratification")

        # Publication coverage (SR-0096): a normative item referenced by no
        # published document is scope that can justify itself but cannot reach
        # the reader. Inert (published is None) until [docs] paths are configured.
        if published is not None and item.normative and item.uid not in published:
            add("unpublished", item.uid, f,
                "normative item is referenced by no published document")

        # Quality — grounded but ambiguous is still not deliverable.
        if item.attrs.get("ambiguous"):
            reasons = "; ".join(item.attrs.get("suspect_reasons", [])) or "flagged ambiguous"
            add("ambiguous", item.uid, f, reasons)

        # Suspect links (SR-0034): stored stamp != target's current fingerprint.
        for link in item.links:
            if link.stamp is None or _is_external(link.target):
                continue
            target = project.get(link.target)
            if target is None:
                continue
            if fingerprint(target, schema) != link.stamp:
                add("suspect-link", item.uid, f,
                    f"link to {link.target} ({link.type}) is suspect — target changed since last confirmed")

        # Review drift (SR-0038): reviewed fingerprint != current.
        if item.reviewed is not None and fingerprint(item, schema) != item.reviewed:
            add("unreviewed", item.uid, f, "content changed since last review")

    out.extend(_coverage_rules(project, idx, strict))
    return out


def _coverage_rules(project, idx: Index, strict: bool) -> list[Finding]:
    """Declared coverage rules (SR-0042): items matching `filter` must have an
    incoming/outgoing link of a given type."""
    findings: list[Finding] = []
    schema = project.schema
    for rule in schema.coverage:
        needs = rule.get("needs", "")
        m = re.match(r"(incoming|outgoing):(\w+)", needs)
        if not m:
            continue
        direction, ltype = m.group(1), m.group(2)
        sev = rule.get("severity", WARNING)
        if strict and sev == WARNING:
            sev = ERROR
        if sev == OFF:
            continue
        for item in project.items():
            if item.is_deleted or not _match_filter(item, rule.get("filter", "")):
                continue
            links = (idx.in_links(item.uid, {ltype}) if direction == "incoming"
                     else idx.out_links(item.uid, {ltype}))
            if not links:
                findings.append(Finding("coverage", sev, item.uid, _file(item),
                                        f"needs {direction} '{ltype}' link ({needs})"))
    return findings


class FilterError(ValueError):
    """A filter expression could not be evaluated (SR-0045)."""


def _filter_namespace(item) -> dict:
    """The one set of names an SR-0045 filter can reference. Shared by coverage
    rules and the `query` CLI so the language is identical in both."""
    return {
        "type": item.type, "status": item.status, "doc": item._doc_prefix,
        "uid": item.uid, "derived": item.derived, "normative": item.normative,
        "title": item.title, "text": item.text, "rationale": item.rationale,
        "attrs": dict(item.attrs),  # e.g. attrs.get('priority') == 'must'
        "true": True, "false": False,
    }


def eval_filter(item, expr: str) -> bool:
    """Evaluate an SR-0045 boolean filter against one item. Raises FilterError
    on a malformed expression so user-facing callers can report it. The
    namespace omits builtins; expressions are the user's own (local, offline)."""
    if not expr or not expr.strip():
        return True
    try:
        return bool(eval(expr, {"__builtins__": {}},  # noqa: S307 - constrained ns
                         _filter_namespace(item)))
    except Exception as e:  # noqa: BLE001 - surface as a typed error
        raise FilterError(str(e)) from e


def _match_filter(item, expr: str) -> bool:
    """Lenient variant for config-declared coverage rules: a malformed rule
    filter simply fails to match rather than aborting the whole check."""
    try:
        return eval_filter(item, expr)
    except FilterError:
        return False


def _is_external(target: str) -> bool:
    return "://" in target or "/" in target or "#" in target


class _dummy:
    _path = None
