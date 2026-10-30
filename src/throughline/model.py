# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Core in-memory model — pure objects, no I/O (arch doc 07 §2).

The on-disk format is normative (spec doc 06); these dataclasses mirror it.
Unknown keys are preserved verbatim via ``extra`` so read-modify-write never
loses user content (NFR-0009).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .schema import Schema
from .signatures import RATIFICATIONS, WITHDRAWALS, lift_legacy

if TYPE_CHECKING:
    from .inherit import Inheritance

# Built-in core fields every item reserves (SR-0022). Anything else a project
# defines lives under ``attrs`` (SR-0020). The ratification record is two of them
# since format 4 (SR-0254): the signatures an item carries and the withdrawals.
CORE_FIELDS = [
    "uid", "type", "status", "title", "text", "rationale",
    "normative", "derived", "order", "links", "attrs",
    RATIFICATIONS, WITHDRAWALS, "reviewed",
    "created", "modified", "deleted", "key",
]


@dataclass
class Link:
    """A typed, directed edge stored on the source item (SR-0030)."""
    target: str
    type: str
    stamp: str | None = None          # target fingerprint when last confirmed (SR-0034)
    # The target as the item's own file writes it, set by a tool that rewrites
    # targets to merge the item into a wider graph (SR-0245).
    _authored_target: str | None = field(default=None, repr=False, compare=False)

    @property
    def authored_target(self) -> str:
        return self._authored_target or self.target

    def to_dict(self) -> dict:
        d = {"target": self.target, "type": self.type}
        if self.stamp is not None:
            d["stamp"] = self.stamp
        return d


@dataclass
class Item:
    uid: str
    type: str
    status: str = "draft"
    title: str = ""
    text: str = ""
    rationale: str = ""
    normative: bool = True
    derived: bool = False
    order: float | None = None
    links: list[Link] = field(default_factory=list)
    attrs: dict = field(default_factory=dict)
    # The ratification record (SR-0254): one signature per person who has signed,
    # oldest first, and one record per signature withdrawn (SR-0262). Written only
    # by ratify, withdraw and migration (SR-0263); see :mod:`signatures`.
    ratifications: list[dict] = field(default_factory=list)
    withdrawals: list[dict] = field(default_factory=list)
    reviewed: str | None = None       # own fingerprint at last review (SR-0038)
    created: str | None = None
    modified: str | None = None
    deleted: dict | None = None       # {date, reason, fingerprint} tombstone (SR-0012)
    # The key the item was created under (SR-0280): a UUID version 4 that a change
    # set or `tl new` gave it, so the same creation asked for again finds this item
    # instead of making a second. Outside the fingerprint and the attributes: it
    # says how the item came to be, not what it binds.
    key: str | None = None
    extra: dict = field(default_factory=dict)
    _path: Path | None = None
    _register_prefix: str | None = None
    # Structural problems found while parsing this item's raw dict (e.g. a link
    # entry missing its target). Carried here so the loader can surface them as
    # named `check` findings instead of the loader crashing (SR-0134).
    _load_errors: list[str] = field(default_factory=list)
    # The UID this item was authored under in its own graph, set only by a tool
    # that re-labels a borrowed item to merge it into a wider graph (SR-0154).
    # `fingerprint` identifies the item by this when present, so the label a
    # consumer chooses cannot invalidate a stamp written where it was authored.
    _authored_uid: str | None = None
    # The normative attribute names declared by the graph that authored this item,
    # set only by a tool that merges a borrowed item into a wider graph (SR-0162).
    # `fingerprint` hashes these when present, so the attributes a consuming graph
    # happens to mark normative cannot invalidate a stamp written elsewhere. An
    # empty tuple is meaningful — "that graph marked none" — so this is None when
    # unset rather than defaulting to ().
    _authored_normative_attrs: tuple[str, ...] | None = None
    # The link types the graph that authored this item declares as signed, set by
    # the same tool for the same reason (SR-0245). None when unset; an empty tuple
    # means that graph signs none.
    _authored_signed_link_types: tuple[str, ...] | None = None

    @property
    def authored_uid(self) -> str:
        """The identity this item was authored under — its own UID unless a
        composing tool re-labelled it, in which case the UID it holds in the
        graph it came from (SR-0154)."""
        return self._authored_uid or self.uid

    @property
    def is_deleted(self) -> bool:
        return self.status == "deleted"

    @staticmethod
    def _parse_links(raw: object) -> tuple[list["Link"], list[str]]:
        """Parse the raw ``links`` value defensively (SR-0134). A malformed entry
        — not a mapping, or missing its required ``target`` — is skipped and its
        reason collected, so a hand-edited file (e.g. `to:` instead of `target:`)
        yields a named `check` finding rather than crashing the loader."""
        links: list[Link] = []
        errors: list[str] = []
        if not isinstance(raw, list):
            errors.append(f"'links' must be a list, got {type(raw).__name__}")
            return links, errors
        for i, l in enumerate(raw, start=1):
            if not isinstance(l, dict):
                errors.append(f"link entry #{i} is not a mapping")
                continue
            target = l.get("target")
            if not isinstance(target, str) or not target:
                errors.append(f"link entry #{i} missing required key 'target'")
                continue
            links.append(Link(target=target, type=l.get("type", "relates"),
                              stamp=l.get("stamp")))
        return links, errors

    @staticmethod
    def _parse_records(raw: object, name: str) -> tuple[list[dict], list[str]]:
        """Parse ``ratifications`` or ``withdrawals`` defensively (SR-0134): a list
        of mappings, each kept as written. Anything else is a named finding."""
        if raw is None:
            return [], []
        if not isinstance(raw, list):
            return [], [f"'{name}' must be a list, got {type(raw).__name__}"]
        records: list[dict] = []
        errors: list[str] = []
        for i, entry in enumerate(raw, start=1):
            if not isinstance(entry, dict):
                errors.append(f"{name} entry #{i} is not a mapping")
                continue
            records.append(entry)
        return records, errors

    @classmethod
    def from_dict(cls, d: dict, path: Path | None = None) -> "Item":
        links, load_errors = cls._parse_links(d.get("links", []) or [])
        attrs = d.get("attrs", {}) or {}
        # A format-3 flat record is lifted into the list as the item is read
        # (SR-0255), so an older graph, a file re-read from history and a file an
        # older Tool wrote all present one shape. The lifted entry is placed first:
        # it is the oldest a file can hold. Nothing is invented by the lift.
        lifted, withdrawn = lift_legacy(attrs) if isinstance(attrs, dict) else ([], [])
        ratifications, errs = cls._parse_records(d.get(RATIFICATIONS), RATIFICATIONS)
        load_errors += errs
        withdrawals, errs = cls._parse_records(d.get(WITHDRAWALS), WITHDRAWALS)
        load_errors += errs
        item = cls(
            uid=d["uid"], type=d["type"], status=d.get("status", "draft"),
            title=d.get("title", ""), text=d.get("text", ""),
            rationale=d.get("rationale", ""),
            normative=d.get("normative", True), derived=d.get("derived", False),
            order=d.get("order"), links=links, attrs=attrs,
            ratifications=lifted + ratifications,
            withdrawals=withdrawn + withdrawals,
            reviewed=d.get("reviewed"), created=d.get("created"),
            modified=d.get("modified"), deleted=d.get("deleted"),
            key=d.get("key"),
            extra={k: v for k, v in d.items() if k not in CORE_FIELDS},
            _path=path,
        )
        item._load_errors = load_errors
        return item

    def to_dict(self) -> dict:
        """Emit in canonical key order (SR-0072). Omits empties to keep files
        terse and diffs minimal, but always keeps identity + statement fields."""
        d: dict = {"uid": self.uid, "type": self.type, "status": self.status}
        if self.title:
            d["title"] = self.title
        d["text"] = self.text
        if self.rationale:
            d["rationale"] = self.rationale
        d["normative"] = self.normative
        if self.derived:
            d["derived"] = self.derived
        if self.order is not None:
            d["order"] = self.order
        if self.links:
            d["links"] = [l.to_dict() for l in self.links]
        if self.attrs:
            d["attrs"] = self.attrs
        if self.ratifications:
            d[RATIFICATIONS] = self.ratifications
        if self.withdrawals:
            d[WITHDRAWALS] = self.withdrawals
        if self.reviewed is not None:
            d["reviewed"] = self.reviewed
        if self.created:
            d["created"] = self.created
        if self.modified:
            d["modified"] = self.modified
        if self.deleted is not None:
            d["deleted"] = self.deleted
        if self.key is not None:
            d["key"] = self.key
        d.update(self.extra)
        return d


@dataclass
class Register:
    """A prefix-owning, numbered collection of items on disk (the folder with a
    ``.register.yml`` manifest). A register owns a UID prefix and its numbering
    (SR-0002). It may declare the item ``type`` it holds, which is what `tl new`
    gives an item born in it when no ``--type`` is named (SR-0241). Distinct from
    a published *document* — the reader-facing Markdown that ``tl docs`` injects
    into."""
    prefix: str
    title: str = ""
    type: str | None = None
    digits: int = 4
    parent: str | None = None
    reserved: list[int] = field(default_factory=list)
    sections: list[dict] = field(default_factory=list)
    path: Path | None = None
    items: dict[str, Item] = field(default_factory=dict)
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_manifest(cls, d: dict, path: Path | None = None) -> "Register":
        known = {"prefix", "digits", "title", "type", "parent", "reserved",
                 "sections"}
        return cls(
            prefix=d["prefix"], title=d.get("title", ""), type=d.get("type"),
            digits=d.get("digits", 4), parent=d.get("parent"),
            reserved=d.get("reserved", []) or [], sections=d.get("sections", []) or [],
            path=path, extra={k: v for k, v in d.items() if k not in known},
        )

    def manifest_dict(self) -> dict:
        d: dict = {"prefix": self.prefix, "digits": self.digits}
        if self.title:
            d["title"] = self.title
        if self.type:
            d["type"] = self.type
        if self.parent:
            d["parent"] = self.parent
        if self.reserved:
            d["reserved"] = sorted(self.reserved)
        if self.sections:
            d["sections"] = self.sections
        d.update(self.extra)
        return d


@dataclass
class Project:
    path: Path
    config: dict = field(default_factory=dict)
    registers: dict[str, Register] = field(default_factory=dict)
    # UIDs seen more than once *within a single register folder* on disk. The
    # per-register ``items`` dict folds duplicates into one entry, so a
    # same-folder merge clash (two files both declaring SR-0001) would be
    # silently lost; the loader records it here so ``uid.collisions()`` can
    # still surface it (SR-0006).
    duplicate_uids: set[str] = field(default_factory=set)
    # Prefixes declared by more than one register folder on disk, mapped to every
    # declaring directory. A prefix owns a UID namespace (SR-0002), so a shared
    # prefix overlaps numbering and makes the loader drop one folder's items; the
    # loader records the clash here so ``check`` can fail fast (SR-0101).
    prefix_conflicts: dict[str, list[str]] = field(default_factory=dict)
    # Structural problems found while parsing items on disk (e.g. a link entry
    # missing its target), as (uid, file, message). The loader records them here
    # rather than crashing, so `check` reports each as a named finding (SR-0134).
    load_errors: list[tuple[str, str, str]] = field(default_factory=list)
    # Roots of other projects found in this project's tree, each a directory
    # holding a throughline.toml of its own, where the loader stopped rather than
    # read them as part of this one (SR-0239). Hidden directories are not searched,
    # so none is recorded from inside one.
    nested_projects: list[Path] = field(default_factory=list)
    # The configuration the schema is built from when the project takes its schema
    # from a source (SR-0275): the source's schema with this project's own
    # declarations added. ``None`` when there is no schema source, and the schema
    # is built from ``config`` alone. ``config`` stays exactly what this project's
    # own file says, since that is the only file anything here may write.
    schema_config: dict | None = None
    # Where each inherited declaration came from (SR-0279); ``None`` likewise.
    inheritance: "Inheritance | None" = None
    _schema: Schema | None = field(default=None, repr=False, compare=False)

    @property
    def schema(self) -> Schema:
        """The one validated view of this project's config (SR-0082). Built on
        first access and cached; every component reads its helpers instead of
        the raw config dict."""
        if self._schema is None:
            self._schema = Schema.from_config(self.formed_config)
        return self._schema

    @property
    def formed_config(self) -> dict:
        """The configuration the schema is built from: ``schema_config`` where the
        project takes its schema from a source (SR-0275), else ``config``. Read the
        schema's tables from here, never from ``config``, which holds only what
        this project's own file declares."""
        return self.config if self.schema_config is None else self.schema_config

    def shell(self, path: Path | None = None) -> "Project":
        """An empty project with this one's configuration and schema, for a view
        or a union built over it."""
        return Project(path=self.path if path is None else path, config=self.config,
                       schema_config=self.schema_config, inheritance=self.inheritance)

    def items(self):
        for reg in self.registers.values():
            yield from reg.items.values()

    def relied_on_statuses(self) -> set[str]:
        """Every status this project would strand by not declaring it: the ones
        its items are in, and the ones the rest of its own configuration already
        names — which the schema itself refuses to build without.

        This is the one status vocabulary guaranteed to leave the project exactly
        as valid as it is now, so it is what the gate proposes to a project that
        declares none and what the migration backfill writes (SR-0185)."""
        schema = self.schema
        used = {i.status for i in self.items()}
        used |= set((schema.status_roles or {}).values())
        for frm, to in (schema.transitions or {}).items():
            used |= {frm} | set(to)
        return used

    def suspect_unreachable_statuses(self) -> list[str]:
        """The live statuses this project's lifecycle gives no route to the
        suspect status, sorted (SR-0174, SR-0188).

        Suspicion propagation is how the withdrawal of an item's footing becomes
        visible, and it is carried entirely by the transition table: a status with
        no declared move to suspect silently opts every item sitting in it out of
        the mechanism. Empty when there is nothing to answer for — a project that
        declares no transitions is unconstrained, and one that binds no suspect
        role has no such mechanism to disable.

        Judged over the statuses an item can actually hold (SR-0177): a composing
        project declares the statuses its *borrowed* items carry so the union
        validates, and nothing local ever enters them; reporting those buries the
        real gaps under noise the project cannot act on. Occupancy is taken
        alongside reachability so a status set by hand, or left behind by a
        lifecycle that has since changed, is still judged rather than excused —
        but counted over locally-authored items only (SR-0178), because a tool that
        composes by merging borrowed items would otherwise reinstate through
        occupancy exactly what reachability excluded, and a borrowed status answers
        to its own graph's table, which this project cannot edit.

        Defined here, once, because two callers act on it and they must never
        disagree: the gate reports each of these as a finding, and the migration
        repair adds the missing route (SR-0188). Were the two notions of "live" to
        drift, migration would either churn a project's configuration over
        statuses the gate ignores, or leave the ones it reports."""
        schema = self.schema
        if schema.transitions is None or not (schema.status_roles or {}).get("suspect"):
            return []
        suspect = schema.status_roles["suspect"]
        dead = schema.dead_statuses()
        occupied = {i.status for i in self.items() if i._authored_uid is None}
        reachable = schema.reachable_statuses()
        declared = schema.statuses or (set(schema.transitions) | occupied)
        live = declared if reachable is None else (reachable | occupied) & declared
        return sorted(s for s in live
                      if s != suspect and s not in dead
                      and not schema.allows_transition(s, suspect))

    def relied_on_link_types(self) -> set[str]:
        """The link types this project would strand by not declaring them — see
        :meth:`relied_on_statuses`. Its configuration names them in three further
        places, and the schema will not build while a grounding or endpoint rule
        cites a type the declared vocabulary leaves out."""
        schema = self.schema
        used = {link.type for item in self.items() for link in item.links}
        return (used | set(schema.ground_link_types)
                | set(schema.suspect_link_types) | set(schema.link_rules))

    def get(self, uid: str) -> Item | None:
        for reg in self.registers.values():
            if uid in reg.items:
                return reg.items[uid]
        return None

    def register_of(self, uid: str) -> Register | None:
        for reg in self.registers.values():
            if uid in reg.items:
                return reg
        return None
