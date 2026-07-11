# Copyright (c) 2026 Time Back Solutions Limited
# SPDX-License-Identifier: Apache-2.0
"""Core in-memory model — pure objects, no I/O (arch doc 07 §2).

The on-disk format is normative (spec doc 06); these dataclasses mirror it.
Unknown keys are preserved verbatim via ``extra`` so read-modify-write never
loses user content (NFR-0009).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .schema import Schema

# Built-in core fields every item reserves (SR-0022). Anything else a project
# defines lives under ``attrs`` (SR-0020).
CORE_FIELDS = [
    "uid", "type", "status", "title", "text", "rationale",
    "normative", "derived", "order", "links", "attrs", "reviewed",
    "created", "modified", "deleted",
]


@dataclass
class Link:
    """A typed, directed edge stored on the source item (SR-0030)."""
    target: str
    type: str
    stamp: str | None = None          # target fingerprint when last confirmed (SR-0034)

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
    reviewed: str | None = None       # own fingerprint at last review (SR-0038)
    created: str | None = None
    modified: str | None = None
    deleted: dict | None = None       # {date, reason} tombstone payload (SR-0012)
    extra: dict = field(default_factory=dict)
    _path: Path | None = None
    _doc_prefix: str | None = None

    @property
    def is_deleted(self) -> bool:
        return self.status == "deleted"

    @classmethod
    def from_dict(cls, d: dict, path: Path | None = None) -> "Item":
        links = [Link(target=l["target"], type=l.get("type", "relates"),
                      stamp=l.get("stamp")) for l in d.get("links", []) or []]
        return cls(
            uid=d["uid"], type=d["type"], status=d.get("status", "draft"),
            title=d.get("title", ""), text=d.get("text", ""),
            rationale=d.get("rationale", ""),
            normative=d.get("normative", True), derived=d.get("derived", False),
            order=d.get("order"), links=links, attrs=d.get("attrs", {}) or {},
            reviewed=d.get("reviewed"), created=d.get("created"),
            modified=d.get("modified"), deleted=d.get("deleted"),
            extra={k: v for k, v in d.items() if k not in CORE_FIELDS},
            _path=path,
        )

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
        if self.reviewed is not None:
            d["reviewed"] = self.reviewed
        if self.created:
            d["created"] = self.created
        if self.modified:
            d["modified"] = self.modified
        if self.deleted is not None:
            d["deleted"] = self.deleted
        d.update(self.extra)
        return d


@dataclass
class Document:
    prefix: str
    title: str = ""
    digits: int = 4
    parent: str | None = None
    reserved: list[int] = field(default_factory=list)
    sections: list[dict] = field(default_factory=list)
    path: Path | None = None
    items: dict[str, Item] = field(default_factory=dict)
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_manifest(cls, d: dict, path: Path | None = None) -> "Document":
        known = {"prefix", "digits", "title", "parent", "reserved", "sections"}
        return cls(
            prefix=d["prefix"], title=d.get("title", ""),
            digits=d.get("digits", 4), parent=d.get("parent"),
            reserved=d.get("reserved", []) or [], sections=d.get("sections", []) or [],
            path=path, extra={k: v for k, v in d.items() if k not in known},
        )

    def manifest_dict(self) -> dict:
        d: dict = {"prefix": self.prefix, "digits": self.digits}
        if self.title:
            d["title"] = self.title
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
    documents: dict[str, Document] = field(default_factory=dict)
    _schema: Schema | None = field(default=None, repr=False, compare=False)

    @property
    def schema(self) -> Schema:
        """The one validated view of this project's config (SR-0082). Built on
        first access and cached; every component reads its helpers instead of
        the raw config dict."""
        if self._schema is None:
            self._schema = Schema.from_config(self.config)
        return self._schema

    def items(self):
        for doc in self.documents.values():
            yield from doc.items.values()

    def get(self, uid: str) -> Item | None:
        for doc in self.documents.values():
            if uid in doc.items:
                return doc.items[uid]
        return None

    def document_of(self, uid: str) -> Document | None:
        for doc in self.documents.values():
            if uid in doc.items:
                return doc
        return None
