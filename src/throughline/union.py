# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Build one throughline graph from a consumer plus its declared sources (SR-0230; throughline-compose SR-0004).

The core throughline validator is reused *unchanged* (SR-0004): rather than teach
`tl` about namespaces, `tl-compose` folds every source's items into a single union
:class:`~throughline.model.Project` and runs the ordinary `validate` over it. The
one trick is identity. A source's UIDs are its own (SR-0002), so two sources — or a
source and the consumer — may both hold ``SR-0001``. Before merging, each borrowed
UID is *mangled* to a synthetic prefix derived from its namespace (``gds:SR-0001``
→ ``GDSSR-0001``), so the union has globally-unique, grammar-valid UIDs. Every
namespace-qualified reference (SR-0001) and every source-internal reference is
rewritten to the mangled form, so the graph resolves with no colons left.

Findings from the core are then translated back: mangled UIDs in a finding's target
and message become their original ``<namespace>:<UID>`` form, so the composer reads
diagnostics in the vocabulary they wrote.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

from .validate import is_external, is_namespace_qualified
from .model import Item, Project, Register
from .uid import UID_RE, parse_uid

# The prefix a mangled UID may occupy: core UID grammar (throughline SR-0001).
_PREFIX_RE = re.compile(r"^[A-Z][A-Z0-9]{1,15}$")
# Split a namespace-qualified reference into (namespace, uid).
_NS_REF_RE = re.compile(r"^([a-z][a-z0-9_-]*):(.+)$")


class ComposeError(Exception):
    """Composition cannot proceed — an unbound namespace, a UID that will not
    mangle to a legal prefix, or a synthetic-prefix clash. Fail fast (SR-0005)."""


@dataclass
class Union:
    project: Project                    # merged graph, governed by consumer schema
    owners: dict[str, tuple[str, str]]  # synthetic prefix -> (namespace, source prefix)

    def qualified(self, uid: str) -> str:
        """Reconstruct the original ``<namespace>:<UID>`` for any union UID whose
        prefix is a mangled one — including a reference to a source clause that
        does *not* exist, so a dangling cross-source link reads in the composer's
        own vocabulary. A consumer-local UID is returned unchanged."""
        m = UID_RE.match(uid)
        if m and m.group(1) in self.owners:
            namespace, src_prefix = self.owners[m.group(1)]
            return f"{namespace}:{src_prefix}-{m.group(2)}"
        return uid

    def displayed(self) -> Project:
        """The union as the composer names it (SR-0037): a copy in which every
        borrowed UID, link target and register prefix reads back as
        ``<namespace>:<...>``.

        A listing selects with a filter and then prints what it selected, so those
        two have to speak one vocabulary. Printing ``wcag:UR-0012`` while matching
        only ``WCAGUR-0012`` would hand the composer a second silent zero one layer
        below the one this view exists to remove — and it would leak the mangled
        prefix, which is an internal identity trick (SR-0004) this tool does not
        promise to keep.

        The view is never validated and its UIDs are deliberately not legal core
        UIDs; the union it is taken from is untouched.
        """
        out = Project(path=self.project.path, config=self.project.config)
        for prefix, reg in self.project.registers.items():
            owner = self.owners.get(prefix)
            shown = f"{owner[0]}:{owner[1]}" if owner else prefix
            items = {}
            for uid, it in reg.items.items():
                qualified = self.qualified(uid)
                items[qualified] = replace(
                    it,
                    uid=qualified,
                    links=[replace(link, target=self.qualified(link.target))
                           for link in it.links],
                    _register_prefix=shown,
                )
            out.registers[shown] = replace(reg, prefix=shown, items=items)
        return out

    def pattern(self) -> re.Pattern | None:
        """A regex matching any mangled UID token, for message translation."""
        if not self.owners:
            return None
        prefixes = "|".join(re.escape(p) for p in
                            sorted(self.owners, key=len, reverse=True))
        return re.compile(rf"(?:{prefixes})-[0-9]+")


def _sanitize_ns(namespace: str) -> str:
    """The uppercase, alphanumeric-only stem a namespace contributes to a mangled
    prefix. A namespace starts with a lowercase letter (sources.py), so the stem
    always starts with a letter and is a legal prefix head."""
    return re.sub(r"[^A-Z0-9]", "", namespace.upper())


class _Mangler:
    """Deterministic, collision-checked mapping from ``(namespace, source UID)`` to
    a unique, grammar-valid union UID. Only the prefix changes; the number is
    preserved verbatim so the width and value survive."""

    def __init__(self) -> None:
        # synthetic prefix -> (namespace, source prefix) that owns it, to catch two
        # distinct (namespace, prefix) pairs colliding on one synthetic prefix.
        self.owners: dict[str, tuple[str, str]] = {}

    def prefix(self, namespace: str, src_prefix: str) -> str:
        syn = _sanitize_ns(namespace) + src_prefix
        if not _PREFIX_RE.match(syn):
            raise ComposeError(
                f"namespace '{namespace}' + prefix '{src_prefix}' mangles to "
                f"'{syn}', which is not a legal UID prefix (max 16 chars) — "
                "import the source under a shorter namespace")
        owner = self.owners.setdefault(syn, (namespace, src_prefix))
        if owner != (namespace, src_prefix):
            raise ComposeError(
                f"namespaces '{owner[0]}' and '{namespace}' both mangle prefix "
                f"'{src_prefix}' to '{syn}' — import one under a distinct namespace")
        return syn

    def uid(self, namespace: str, uid: str) -> str:
        m = UID_RE.match(uid)
        if not m:
            return uid  # malformed target — leave for the core to flag as dangling
        pfx, num = m.group(1), m.group(2)
        return f"{self.prefix(namespace, pfx)}-{num}"


def _rewrite_target(target: str, current_ns: str | None,
                    namespaces: set[str], mangler: _Mangler,
                    labels: dict[str, str], item_uid: str) -> str:
    """Map one link target into union space.

    - External pointers (URLs, paths, anchors — SR-0031) stay opaque.
    - A namespace-qualified ``ns:UID`` written by the *consumer* resolves to the
      mangled UID of the namespace bound under that label; a label nothing binds
      is a fail-fast :class:`ComposeError` naming the item and what is bound.
    - A namespace-qualified ``ns:UID`` written inside a *source* resolves through
      that source's own label map (SR-0045): the label as the source wrote it,
      mapped to the union namespace its declaration was bound under, through any
      alias the consumer set. It never falls through to the union's namespace
      set — a label the consumer happens to reuse for a different source must not
      capture a reference written against it. A label the source's own
      configuration does not declare fails fast, naming the source.
    - A bare UID inside a *source* item is a source-internal reference and mangles
      into that source's namespace; inside the *consumer* it is a local UID and is
      left untouched.
    """
    if is_external(target):
        return target
    if is_namespace_qualified(target):
        m = _NS_REF_RE.match(target)
        ns, uid = m.group(1), m.group(2)
        if current_ns is not None:
            mapped = labels.get(ns)
            if mapped is None:
                raise ComposeError(
                    f"source '{current_ns}' cites '{target}' on its {item_uid} "
                    f"against a label it does not declare — its own "
                    f"throughline.toml must declare '{ns}' as a source")
            return mangler.uid(mapped, uid)
        if ns not in namespaces:
            bound = ", ".join(sorted(namespaces)) if namespaces else "nothing"
            raise ComposeError(
                f"{item_uid} cites '{target}', but nothing binds a namespace "
                f"'{ns}'. Bound: {bound}.")
        return mangler.uid(ns, uid)
    if current_ns is not None:
        return mangler.uid(current_ns, target)
    return target


def _rewrite_links(item: Item, current_ns: str | None,
                   namespaces: set[str], mangler: _Mangler,
                   labels: dict[str, str]) -> Item:
    shown = item.uid if current_ns is None else f"{current_ns}:{item.uid}"
    new_links = [replace(link, target=_rewrite_target(
        link.target, current_ns, namespaces, mangler, labels, shown))
        for link in item.links]
    return replace(item, links=new_links)


def build_union(consumer: Project, sources: dict[str, Project],
                labels: dict[str, dict[str, str]] | None = None) -> Union:
    """Fold ``sources`` (namespace -> loaded source project) into ``consumer`` and
    return the merged :class:`Union`. The union is governed by the *consumer's*
    schema — the consumer decides which types, links and statuses are legal for
    the composed graph.

    ``labels`` (SR-0045) gives, per bound namespace, the map from the labels that
    source's own references use to the union namespaces they were bound under —
    what its own ``[[sources]]`` declared, and every label its own sources hoisted
    into it, each renamed through any alias the consumer set on the declared source
    carrying it. A source's cross-source reference is resolved only through its own
    entry, so a source with no entry may cite no other namespace."""
    namespaces = set(sources)
    label_maps = labels or {}
    mangler = _Mangler()

    union = Project(path=consumer.path, config=consumer.config)

    # Consumer items keep their own UIDs; only their ns-qualified references are
    # rewritten. Copy registers so the loaded consumer objects stay untouched.
    for prefix, reg in consumer.registers.items():
        items = {uid: _rewrite_links(it, None, namespaces, mangler, {})
                 for uid, it in reg.items.items()}
        union.registers[prefix] = replace(reg, items=items)

    # Each source's items are mangled into namespace-derived prefixes and merged.
    for namespace, source in sources.items():
        own_labels = label_maps.get(namespace, {})
        source_schema = source.schema
        for reg in source.registers.values():
            for uid, it in reg.items.items():
                mangled_uid = mangler.uid(namespace, uid)
                mangled_prefix = parse_uid(mangled_uid)[0]
                rewritten = _rewrite_links(it, namespace, namespaces, mangler,
                                           own_labels)
                # The synthetic UID is ours; the authored one travels with the
                # item so its fingerprint — and any ratification stamped against
                # that fingerprint in the source — survives re-labelling (SR-0024).
                #
                # So does the set of attributes the source's own schema marks
                # normative (SR-0162). Both are inputs to the fingerprint and both
                # are otherwise supplied by *this* union — the label we chose and
                # the consumer's schema we validate under — so without carrying
                # them the stamp on borrowed content would depend on who was
                # reading it. It would also leave a consumer no way to compose a
                # source without mirroring that source's normative flags, which is
                # the source dictating what counts as a change in the consumer's
                # own graph.
                merged = replace(rewritten, uid=mangled_uid,
                                 _register_prefix=mangled_prefix,
                                 _authored_uid=uid,
                                 _authored_normative_attrs=tuple(
                                     source_schema.normative_attrs(it.type)))
                target = union.registers.get(mangled_prefix)
                if target is None:
                    if mangled_prefix in consumer.registers:
                        raise ComposeError(
                            f"source '{namespace}' mangles to prefix "
                            f"'{mangled_prefix}', which the consumer already uses — "
                            "import the source under a distinct namespace")
                    target = Register(prefix=mangled_prefix,
                                      title=f"{namespace}:{reg.prefix}")
                    union.registers[mangled_prefix] = target
                target.items[mangled_uid] = merged

    return Union(project=union, owners=mangler.owners)


def translate_finding(finding, union: Union, pattern: re.Pattern | None):
    """Rewrite a core :class:`~throughline.validate.Finding` back into namespace
    vocabulary: its ``uid`` and any mangled UID token in its ``message`` become the
    original ``<namespace>:<UID>``. Returns a shallow copy; the original is
    untouched."""
    message = finding.message
    if pattern is not None:
        message = pattern.sub(lambda m: union.qualified(m.group(0)), message)
    return replace(finding, uid=union.qualified(finding.uid), message=message)
