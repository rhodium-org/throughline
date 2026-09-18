# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Declared external sources (SR-0230; throughline-compose SR-0001, SR-0002, SR-0006, SR-0045).

A consuming project names the throughline sources it composes in an array of
``[[sources]]`` tables in its ``throughline.toml``. Each entry binds an
importer-chosen *namespace* (SR-0001) to a standalone throughline source whose UIDs
are its own (SR-0002). Clauses are then referenced from the consumer as
``<namespace>:<UID>``.

A source is located one of two ways (SR-0006):

- ``url`` + ``ref`` — a git origin pinned to an edition (normally a tag). The
  durable, shareable form; resolved into a per-user cache by ``resolve.py``.
- ``path`` — a local directory, for developing a source and its consumer side by
  side.

The two are mutually exclusive; a ``url`` without a ``ref`` is rejected so a
dependency can never silently track a moving default. Either form may carry an
optional ``subdir`` (SR-0008) naming a directory, relative to the fetched
repository root (or the local ``path``), that holds the throughline project — so a
graph living under a subpath of a larger repo is a first-class source.

Composing a source composes what it composes (SR-0045): every source a declared
source itself declares reaches the union, to any depth, under the label its
declaring source gave it. The consumer's one lever over those labels is an
optional ``alias`` table on the declared source, mapping a label used anywhere
beneath that source to a namespace of the consumer's choosing. The ``reexport``
key that SR-0014 introduced is withdrawn: in the consumer's own configuration it
is refused with a pointer to ``alias``; inside a source's configuration it is
ignored, so an edition published with the key stays composable.

This module is pure config parsing — it does not fetch or load anything.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

# The namespace grammar mirrors the core's namespace-qualified reference token
# (SR-0107): a lowercase name a reference can carry before the colon.
_NAMESPACE_RE = re.compile(r"^[a-z][a-z0-9_-]*$")

# The key SR-0014 introduced and SR-0045 withdrew.
WITHDRAWN_KEY = "reexport"


class SourceError(ValueError):
    """A malformed or ambiguous ``[[sources]]`` declaration — fail fast (SR-0005)."""


@dataclass(frozen=True)
class Source:
    namespace: str
    path: str | None = None
    url: str | None = None
    ref: str | None = None
    subdir: str | None = None
    # SR-0045: the consumer's one lever over transitive labels. Maps a label used
    # anywhere in the subtree rooted at this source — the name a source beneath it
    # gave one of its own sources — to the namespace the consumer binds it under.
    # Empty when every label is kept as its declaring source gave it.
    alias: dict[str, str] = field(default_factory=dict)

    @property
    def is_remote(self) -> bool:
        return self.url is not None


def _parse_subdir(ns: str, subdir) -> str | None:
    """Validate an optional ``subdir`` (SR-0008): a relative path within the tree.

    An absolute path, or one that escapes the fetched root with a parent segment,
    fails fast — a source may only compose from inside its own tree.
    """
    if subdir is None:
        return None
    if not isinstance(subdir, str) or not subdir.strip():
        raise SourceError(f"source '{ns}' has a non-string or empty 'subdir'")
    subdir = subdir.strip()
    parts = PurePosixPath(subdir).parts
    if PurePosixPath(subdir).is_absolute() or subdir.startswith(("/", "\\")):
        raise SourceError(
            f"source '{ns}' has an absolute 'subdir' — it must be relative to the "
            "source root (SR-0008)")
    if ".." in parts:
        raise SourceError(
            f"source '{ns}' has a 'subdir' that escapes the source root with '..' "
            "(SR-0008)")
    return subdir


def _parse_alias(ns: str, raw) -> dict[str, str]:
    """Validate an optional ``alias`` table (SR-0045): ``{ label = "name" }``.

    Each key is a label as some source beneath this one declared it; each value is
    the namespace the consumer binds that label to. Both sides must be valid
    namespace names, and two labels may not be aliased to one name — that would ask
    for two sources under a single binding, which is either the collision SR-0015
    refuses or a coalescence the tool reports on its own.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise SourceError(
            f"source '{ns}' has an 'alias' that is not a table of "
            f"label = \"name\" (SR-0045)")
    mapping: dict[str, str] = {}
    for label, name in raw.items():
        if not isinstance(label, str) or not _NAMESPACE_RE.match(label):
            raise SourceError(
                f"source '{ns}' aliases an invalid label '{label}' "
                "(lowercase letter, then letters/digits/-/_)")
        if not isinstance(name, str) or not _NAMESPACE_RE.match(name):
            raise SourceError(
                f"source '{ns}' aliases '{label}' to an invalid namespace '{name}'")
        if name in mapping.values():
            other = next(k for k, v in mapping.items() if v == name)
            raise SourceError(
                f"source '{ns}' aliases both '{other}' and '{label}' to '{name}' — "
                "one alias binds one label")
        mapping[label] = name
    return mapping


def _refuse_withdrawn(ns: str) -> SourceError:
    return SourceError(
        f"source '{ns}' uses '{WITHDRAWN_KEY}', which is withdrawn (SR-0045): every "
        "source a source composes is now composed with it. To rename a transitive "
        "label use alias = { asvs = \"owasp\" } on the declared source that carries it.")


def withdrawn_declarations(project) -> list[str]:
    """The namespaces of ``[[sources]]`` entries in ``project`` that still carry
    the withdrawn ``reexport`` key (SR-0045). Read from a *source's* configuration
    so the summary can say a published edition carries the key; the consumer's own
    configuration refuses it in :func:`parse_sources` instead."""
    raw = project.config.get("sources", [])
    if not isinstance(raw, list):
        return []
    return [e.get("namespace", "?") for e in raw
            if isinstance(e, dict) and WITHDRAWN_KEY in e]


def parse_sources(project, *, withdrawn: str = "refuse") -> list[Source]:
    """Read the ``[[sources]]`` array from a loaded project's config.

    Returns an empty list when none are declared — a project with no sources is
    an ordinary throughline project and ``tl-compose`` behaves exactly like ``tl``
    over it (SR-0003).

    ``withdrawn`` says what a ``reexport`` key means here (SR-0045): ``"refuse"``
    for the consumer's own configuration, the one file the composer can edit;
    ``"ignore"`` for a source's configuration read during the transitive walk,
    where the key is a fact about a published edition and the closure it named is
    composed anyway.
    """
    if withdrawn not in ("refuse", "ignore"):
        raise ValueError(f"withdrawn must be 'refuse' or 'ignore', not {withdrawn!r}")
    raw = project.config.get("sources", [])
    if not isinstance(raw, list):
        raise SourceError("[[sources]] must be an array of tables")
    out: list[Source] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise SourceError(f"[[sources]] entry {i} is not a table")

        ns = entry.get("namespace")
        if not ns or not isinstance(ns, str):
            raise SourceError(f"[[sources]] entry {i} is missing a 'namespace'")
        if not _NAMESPACE_RE.match(ns):
            raise SourceError(
                f"namespace '{ns}' is not a valid namespace name "
                "(lowercase letter, then letters/digits/-/_)")
        if ns in seen:
            raise SourceError(
                f"namespace '{ns}' is declared twice — a namespace binds one source")
        if WITHDRAWN_KEY in entry and withdrawn == "refuse":
            raise _refuse_withdrawn(ns)

        path = entry.get("path")
        url = entry.get("url")
        ref = entry.get("ref")
        subdir = _parse_subdir(ns, entry.get("subdir"))
        alias = _parse_alias(ns, entry.get("alias"))

        has_path = bool(path)
        has_url = bool(url)
        if has_path and has_url:
            raise SourceError(
                f"source '{ns}' declares both 'path' and 'url' — they are mutually "
                "exclusive (SR-0006)")
        if not has_path and not has_url:
            raise SourceError(
                f"source '{ns}' must declare either a 'path' or a 'url'")

        if has_path:
            if not isinstance(path, str):
                raise SourceError(f"source '{ns}' has a non-string 'path'")
            if ref:
                raise SourceError(
                    f"source '{ns}' declares a 'ref' with a local 'path' — a ref "
                    "only pins a 'url' (SR-0006)")
            src = Source(namespace=ns, path=path, subdir=subdir, alias=alias)
        else:
            if not isinstance(url, str):
                raise SourceError(f"source '{ns}' has a non-string 'url'")
            if not ref or not isinstance(ref, str):
                raise SourceError(
                    f"source '{ns}' has a 'url' but no 'ref' — pin the edition with "
                    "a git tag, branch, or commit (SR-0006)")
            src = Source(namespace=ns, url=url, ref=ref, subdir=subdir, alias=alias)

        seen.add(ns)
        out.append(src)
    return out
