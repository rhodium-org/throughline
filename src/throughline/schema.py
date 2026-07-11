# Copyright (c) 2026 Time Back Solutions Limited
# SPDX-License-Identifier: Apache-2.0
"""The project schema — one validated view of throughline.toml (SR-0082).

Everything a project declares about its own shape (item types and their
attributes, the legal link and status vocabularies, the grounding layer, and
rule severities) is parsed once into a single ``Schema`` object. Every other
component — validation, fingerprinting, UID allocation, publishing, and the
CLI — asks this object rather than re-reading the raw config dict, so each
lookup, check, and indirection is defined once and reused. New domain concepts
(types, link types, statuses, rules) can then be added through configuration
alone, without code changes.

Malformed or internally inconsistent configuration raises :class:`SchemaError`
at load time so the tool fails fast instead of silently mis-behaving.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

ERROR, WARNING, OFF = "error", "warning", "off"

# Grounding defaults when a project declares no [grounding] table. Kept here so
# the grounding layer and the schema agree on one source of truth.
_GROUNDING_DEFAULTS = {
    "root_types": ["intent", "business_need", "risk", "constraint", "assumption"],
    "delivery_roots": ["intent", "business_need", "risk", "constraint"],
    "ground_link_types": ["derives_from", "mitigates", "implements", "verifies"],
    "ai_origins": ["ai", "hybrid"],
}

# Attribute value kinds a project may declare (SR-0020).
_ATTR_KINDS = {"enum", "string", "text", "int", "float", "bool", "date"}


class SchemaError(ValueError):
    """Malformed or internally inconsistent project configuration (SR-0082)."""


@dataclass(frozen=True)
class AttrSpec:
    """One declared attribute of an item type."""
    name: str
    kind: str | None = None            # 'enum' | 'string' | ... | None (free-form)
    required: bool = False
    normative: bool = False            # feeds the content fingerprint (SR-0033)
    values: tuple[str, ...] = ()       # allowed members when kind == 'enum'


@dataclass(frozen=True)
class LinkRule:
    """Permitted endpoint types for one link type (SR-0084). ``None`` on either
    side means that side is unconstrained."""
    frm: frozenset[str] | None = None
    to: frozenset[str] | None = None


@dataclass
class Schema:
    name: str
    types: dict[str, dict[str, AttrSpec]]
    link_types: frozenset[str] | None      # None = unconstrained (any type legal)
    statuses: frozenset[str] | None        # None = unconstrained
    transitions: dict[str, frozenset[str]] | None  # None = every status reachable
    link_rules: dict[str, LinkRule]        # per-link-type endpoint constraints
    root_types: frozenset[str]
    delivery_roots: frozenset[str]
    ground_link_types: frozenset[str]
    ai_origins: frozenset[str]
    coverage: tuple[dict, ...]
    rule_overrides: dict                   # rule name -> configured severity

    # ------------------------------------------------------------------ build

    @classmethod
    def from_config(cls, config: dict | None) -> "Schema":
        config = config or {}

        name = ((config.get("project") or {}).get("name")) or ""

        types: dict[str, dict[str, AttrSpec]] = {}
        for tname, tbody in (config.get("types") or {}).items():
            specs: dict[str, AttrSpec] = {}
            for aname, meta in ((tbody or {}).get("attrs") or {}).items():
                if not isinstance(meta, dict):
                    specs[aname] = AttrSpec(name=aname)   # bare = free-form
                    continue
                kind = meta.get("type")
                values = tuple(meta.get("values", []) or [])
                if kind is not None and kind not in _ATTR_KINDS:
                    raise SchemaError(
                        f"type '{tname}' attribute '{aname}' has unknown "
                        f"type '{kind}' (expected one of {sorted(_ATTR_KINDS)})")
                if kind == "enum" and not values:
                    raise SchemaError(
                        f"type '{tname}' attribute '{aname}' is an enum "
                        "with no 'values'")
                specs[aname] = AttrSpec(
                    name=aname, kind=kind,
                    required=bool(meta.get("required", False)),
                    normative=bool(meta.get("normative", False)),
                    values=values)
            types[tname] = specs

        link_types = frozenset((config.get("links") or {}).get("types", [])) or None
        statuses = frozenset((config.get("status") or {}).get("values", [])) or None

        # [transitions] status = ["next", ...] — the statuses each may move to.
        transitions: dict[str, frozenset[str]] | None = None
        traw = config.get("transitions")
        if traw:
            transitions = {
                frm: frozenset(tos or []) for frm, tos in traw.items()
            }

        # [link_rules] <type> = { from = [...], to = [...] } — endpoint types.
        link_rules: dict[str, LinkRule] = {}
        for ltype, spec in (config.get("link_rules") or {}).items():
            spec = spec or {}
            frm = spec.get("from")
            to = spec.get("to")
            link_rules[ltype] = LinkRule(
                frm=frozenset(frm) if frm is not None else None,
                to=frozenset(to) if to is not None else None,
            )

        g = config.get("grounding") or {}
        root_types = frozenset(g.get("root_types", _GROUNDING_DEFAULTS["root_types"]))
        delivery_roots = frozenset(
            g.get("delivery_roots", _GROUNDING_DEFAULTS["delivery_roots"]))
        ground_link_types = frozenset(
            g.get("ground_link_types", _GROUNDING_DEFAULTS["ground_link_types"]))
        ai_origins = frozenset(g.get("ai_origins", _GROUNDING_DEFAULTS["ai_origins"]))

        rules = config.get("rules") or {}
        coverage = tuple(rules.get("coverage", []) or [])
        rule_overrides = {k: v for k, v in rules.items() if k != "coverage"}

        schema = cls(
            name=name, types=types, link_types=link_types, statuses=statuses,
            transitions=transitions, link_rules=link_rules,
            root_types=root_types, delivery_roots=delivery_roots,
            ground_link_types=ground_link_types, ai_origins=ai_origins,
            coverage=coverage, rule_overrides=rule_overrides,
        )
        schema._check_consistency()
        return schema

    @classmethod
    def from_project(cls, project) -> "Schema":
        return cls.from_config(project.config)

    # ---------------------------------------------------- consistency (SR-0082)

    def _check_consistency(self) -> None:
        """Cross-field checks: a config that can never be satisfied is a bug, so
        surface it now rather than as mystifying findings later."""
        if self.transitions is not None and self.statuses is not None:
            endpoints = set(self.transitions) | {
                to for tos in self.transitions.values() for to in tos}
            unknown = endpoints - self.statuses
            if unknown:
                raise SchemaError(
                    f"[transitions] references status(es) {sorted(unknown)} "
                    "that are not in the declared [status] values")
        if self.link_types is None:
            return
        missing = self.ground_link_types - self.link_types
        if missing:
            raise SchemaError(
                f"[grounding] ground_link_types {sorted(missing)} are not in "
                "the declared [links] types")
        unruled = set(self.link_rules) - self.link_types
        if unruled:
            raise SchemaError(
                f"[link_rules] constrains link type(s) {sorted(unruled)} that "
                "are not in the declared [links] types")
        for rule in self.coverage:
            m = re.match(r"(incoming|outgoing):(\w+)", rule.get("needs", ""))
            if m and m.group(2) not in self.link_types:
                raise SchemaError(
                    f"coverage rule needs link type '{m.group(2)}' which is not "
                    "in the declared [links] types")

    # ------------------------------------------------- helpers (defined once)

    def attrs_for(self, item_type: str) -> dict[str, AttrSpec]:
        return self.types.get(item_type, {})

    def attr(self, item_type: str, name: str) -> AttrSpec | None:
        return self.types.get(item_type, {}).get(name)

    def normative_attrs(self, item_type: str) -> list[str]:
        return sorted(n for n, s in self.attrs_for(item_type).items() if s.normative)

    def is_link_type(self, link_type: str) -> bool:
        return self.link_types is None or link_type in self.link_types

    def link_allowed(self, link_type: str, src_type: str,
                     dst_type: str | None = None) -> str | None:
        """Check a link's endpoint types against the declared [link_rules]
        (SR-0084). Returns ``None`` when the shape is allowed, else a human
        message. ``dst_type=None`` (external or absent target) skips the
        target-side check. A link type with no rule is unconstrained."""
        rule = self.link_rules.get(link_type)
        if rule is None:
            return None
        if rule.frm is not None and src_type not in rule.frm:
            return (f"'{link_type}' link must originate from a "
                    f"{sorted(rule.frm)} item, not {src_type}")
        if dst_type is not None and rule.to is not None and dst_type not in rule.to:
            return (f"'{link_type}' link must point to a {sorted(rule.to)} "
                    f"item, not {dst_type}")
        return None

    def is_status(self, status: str) -> bool:
        return self.statuses is None or status in self.statuses

    def allows_transition(self, frm: str, to: str) -> bool:
        """True if a status may move from ``frm`` to ``to``. Unconstrained when
        no [transitions] table is declared; a status is always allowed to stay
        put; an unlisted source has no outgoing moves."""
        if self.transitions is None or frm == to:
            return True
        return to in self.transitions.get(frm, frozenset())

    def is_root(self, item) -> bool:
        return item.type in self.root_types

    def rule_severity(self, rule: str, default: str, strict: bool) -> str:
        sev = self.rule_overrides.get(rule, default)
        if not isinstance(sev, str):
            sev = default
        if strict and sev == WARNING:
            return ERROR
        return sev
