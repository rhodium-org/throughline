# Copyright (c) 2026 Henry J Grech-Cini
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
    # Link types that withdraw the source's footing when their target is
    # invalidated, *without* conferring grounding (SR-0160). Empty by default: a
    # project that declares none cascades suspicion over its grounding links
    # alone, which is the narrow reading, because a cascade that surprises is the
    # failure this exists to end. 'assumes' is the archetype — an item resting on
    # a falsified assumption has genuinely lost its footing — but it is one string
    # in a vocabulary any project may redefine, so it is declared, never assumed.
    "suspect_link_types": [],
}

# Attribute value kinds a project may declare (SR-0020).
_ATTR_KINDS = {"enum", "string", "text", "int", "float", "bool", "date"}

# Semantic status roles (SR-0131): the tool's operations act on statuses by role,
# never by a value fixed in code. A project maps each role to one of its declared
# statuses in [status.roles]. These keys are the tool's own vocabulary (like the
# attribute kinds above); the mapped *values* are the project's choice.
_STATUS_ROLE_KEYS = frozenset(
    {"initial", "proposed", "ratified", "invalidated", "suspect", "tombstone"})


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
    default: object = None             # declared default applied at creation (SR-0138)


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
    status_roles: dict[str, str] | None    # semantic role -> status (SR-0131)
    transitions: dict[str, frozenset[str]] | None  # None = every status reachable
    link_rules: dict[str, LinkRule]        # per-link-type endpoint constraints
    root_types: frozenset[str]
    delivery_roots: frozenset[str]
    ground_link_types: frozenset[str]
    suspect_link_types: frozenset[str]     # extra links that withdraw footing (SR-0160)
    ai_origins: frozenset[str]
    coverage: tuple[dict, ...]
    rule_overrides: dict                   # rule name -> configured severity
    docs_paths: tuple[str, ...]            # [docs] paths globs (SR-0094/0096)
    # Whether taking accountability also advances the item to the ratified status
    # (SR-0172). True — the default, and every existing project's behaviour — moves
    # it. False records the sign-off and leaves the status where it is, for a graph
    # whose statuses track progress rather than agreement; there the two are
    # orthogonal, and advancing a finished item would fabricate a history. The
    # accountability record written is identical either way.
    ratify_moves_status: bool = True

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
                    values=values, default=meta.get("default"))
            types[tname] = specs

        link_types = frozenset((config.get("links") or {}).get("types", [])) or None
        status_cfg = config.get("status") or {}
        statuses = frozenset(status_cfg.get("values", [])) or None

        # [status.roles] role = "status" — which declared status plays each
        # semantic role the tool's operations act on (SR-0131). Absent = the
        # role vocabulary is inert, like the other optional vocabularies.
        # None (table absent) and {} (table declared, binding nothing) are kept
        # apart: the first is a project that never declared roles, which the gate
        # flags at v3+; the second is a deliberate choice, which it must not
        # (SR-0136). Binding is per role, so a partial table is equally valid —
        # a vocabulary with no honest counterpart for a role leaves it out.
        raw_roles = status_cfg.get("roles")
        status_roles: dict[str, str] | None = None
        if raw_roles is not None:
            unknown = set(raw_roles) - _STATUS_ROLE_KEYS
            if unknown:
                raise SchemaError(
                    f"[status.roles] declares unknown role(s) {sorted(unknown)} "
                    f"(expected a subset of {sorted(_STATUS_ROLE_KEYS)})")
            status_roles = {str(k): str(v) for k, v in raw_roles.items()}

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
        suspect_link_types = frozenset(
            g.get("suspect_link_types", _GROUNDING_DEFAULTS["suspect_link_types"]))
        ai_origins = frozenset(g.get("ai_origins", _GROUNDING_DEFAULTS["ai_origins"]))

        rules = config.get("rules") or {}
        coverage = tuple(rules.get("coverage", []) or [])
        rule_overrides = {k: v for k, v in rules.items() if k != "coverage"}

        docs_paths = tuple((config.get("docs") or {}).get("paths", []) or [])

        # [ratify] moves_status — what ratification does to an item's status
        # (SR-0172). Absent means the historical behaviour, so an upgrade changes
        # nothing; a non-boolean is a configuration error rather than a truthiness
        # coercion, because "no" and "off" would otherwise both mean True.
        ratify_cfg = config.get("ratify") or {}
        if not isinstance(ratify_cfg, dict):
            raise SchemaError("[ratify] must be a table")
        unknown = set(ratify_cfg) - {"moves_status"}
        if unknown:
            raise SchemaError(
                f"[ratify] declares unknown key(s) {sorted(unknown)} — the only key "
                "is 'moves_status'")
        ratify_moves_status = ratify_cfg.get("moves_status", True)
        if not isinstance(ratify_moves_status, bool):
            raise SchemaError(
                "[ratify] moves_status must be true or false, not "
                f"{ratify_moves_status!r}")

        schema = cls(
            name=name, types=types, link_types=link_types, statuses=statuses,
            status_roles=status_roles,
            transitions=transitions, link_rules=link_rules,
            root_types=root_types, delivery_roots=delivery_roots,
            ground_link_types=ground_link_types,
            suspect_link_types=suspect_link_types, ai_origins=ai_origins,
            coverage=coverage, rule_overrides=rule_overrides,
            docs_paths=docs_paths, ratify_moves_status=ratify_moves_status,
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
        if self.status_roles is not None and self.statuses is not None:
            bad = {v for v in self.status_roles.values() if v not in self.statuses}
            if bad:
                raise SchemaError(
                    f"[status.roles] maps to status(es) {sorted(bad)} that are "
                    "not in the declared [status] values")
        if self.link_types is None:
            return
        missing = self.ground_link_types - self.link_types
        if missing:
            raise SchemaError(
                f"[grounding] ground_link_types {sorted(missing)} are not in "
                "the declared [links] types")
        stray = self.suspect_link_types - self.link_types
        if stray:
            raise SchemaError(
                f"[grounding] suspect_link_types {sorted(stray)} are not in "
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

    def status_role(self, role: str) -> str:
        """The status a project has bound to a semantic ``role`` (SR-0131). The
        operations resolve every status they write through this, so no status
        name is fixed in code. Raises when the project has not declared the role,
        so an operation fails fast rather than inventing a value."""
        if role not in _STATUS_ROLE_KEYS:
            raise SchemaError(f"unknown status role '{role}'")
        if not self.status_roles or role not in self.status_roles:
            raise SchemaError(
                f"no status is bound to the '{role}' role — declare it under "
                "[status.roles] in the project configuration")
        return self.status_roles[role]

    def withdrawing_link_types(self) -> frozenset[str]:
        """Link types along which invalidation withdraws the source's footing, and so
        the only ones suspicion travels down (SR-0159/SR-0160).

        The grounding links, because an item that grounds in a withdrawn item has lost
        the ground it stood on, plus whatever further types the project has declared
        under ``suspect_link_types`` — typically its assumption link. Everything else is
        left out: a cross-reference is not a justification, and an item that merely
        points at another for a reader's benefit has lost nothing when that other goes.

        Deliberately *not* what :meth:`Index.impact` reports. A reader asking what
        touches an item is asking a wider question than the tool asking whose
        justification has just been withdrawn, and only the second may restatus items
        on its own — so the two uses of the reachable set stay distinct."""
        return self.ground_link_types | self.suspect_link_types

    def dead_statuses(self) -> frozenset[str]:
        """Statuses that mean the item is no longer live — the 'invalidated' and
        'tombstone' roles (SR-0131). Empty when the roles are not declared, so
        the exclusion is inert like the tool's other optional vocabularies."""
        if not self.status_roles:
            return frozenset()
        return frozenset(
            self.status_roles[r]
            for r in ("invalidated", "tombstone") if r in self.status_roles)

    def allows_transition(self, frm: str, to: str) -> bool:
        """True if a status may move from ``frm`` to ``to``. Unconstrained when
        no [transitions] table is declared; a status is always allowed to stay
        put; an unlisted source has no outgoing moves."""
        if self.transitions is None or frm == to:
            return True
        return to in self.transitions.get(frm, frozenset())

    def birth_statuses(self) -> frozenset[str]:
        """The statuses an item is created in — the 'initial' and 'proposed' roles
        (SR-0131). `tl new` picks between them on the item's origin, so together
        they are every entry point into the lifecycle."""
        if not self.status_roles:
            return frozenset()
        return frozenset(
            self.status_roles[r]
            for r in ("initial", "proposed") if r in self.status_roles)

    def reachable_statuses(self) -> frozenset[str] | None:
        """The statuses an item can arrive at, walking the declared transitions out
        from where items are born (SR-0177).

        A project that composes from sources declares the statuses its *borrowed*
        items carry so the union validates, and nothing local ever enters them. Such
        a status sits in the vocabulary with no route in, and a rule that reasons
        about "every declared status" therefore reasons about states this project
        cannot occupy. Returns ``None`` when the answer is unknowable — no
        transitions to walk, or no declared entry point to walk from — so a caller
        can tell "nothing is reachable" apart from "reachability says nothing here"
        rather than reading an empty set as the former."""
        if self.transitions is None:
            return None
        seeds = self.birth_statuses()
        if not seeds:
            return None
        seen = set(seeds)
        queue = list(seeds)
        while queue:
            for nxt in self.transitions.get(queue.pop(), frozenset()):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return frozenset(seen)

    def is_root(self, item) -> bool:
        return item.type in self.root_types

    def rule_severity(self, rule: str, default: str, strict: bool) -> str:
        sev = self.rule_overrides.get(rule, default)
        if not isinstance(sev, str):
            sev = default
        if strict and sev == WARNING:
            return ERROR
        return sev
