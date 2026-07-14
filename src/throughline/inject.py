# Copyright (c) 2026 Time Back Solutions Limited
# SPDX-License-Identifier: Apache-2.0
"""In-place marker injection (SR-0094) — the drift-free document seam (UR-0024).

A requirements document is a human-owned Markdown file that interleaves the
author's own prose with item content drawn from the graph. The author marks the
places where generated content belongs with HTML comments (invisible on GitHub);
throughline regenerates *only* the marked regions, leaving every other byte of
the file untouched. Because the content is a reference that is re-rendered in
place — never a hand-maintained copy — it cannot silently drift from the graph.

Six directives, each opened by ``<!-- tl:<directive> <arg> -->`` and closed by
``<!-- tl:end -->``:

    tl:item   <UID>     one item rendered as a block
    tl:table  <filter>  a table of items matching an SR-0045 filter
    tl:matrix [<dir>:<link_type>] <filter>  a traceability matrix for matching
              items; an optional incoming:/outgoing:<link_type> selector renders
              the items linked to each match in that direction (SR-0099), e.g.
              ``tl:matrix incoming:implements type == 'user_requirement'``
    tl:count  <filter>  the number of live items matching an SR-0045 filter,
              rendered as a bare integer (SR-0109) — a live tally for a document
              or a README badge that cannot silently drift from the graph
    tl:catalog <filter> every matching item rendered as a full block, in UID order
              (SR-0111) — a self-maintaining master reference document other
              documents cite by UID
    tl:unused <filter>  the matching items no narrative document references
              (SR-0112) — a catalogue mirror does not count as use, so the report
              stays meaningful alongside a full catalogue

Anything richer (HTML, PDF, a whole book) is delegated to external tools
(pandoc, mdBook) run over the injected files. Keeping the engine to these
directives is a deliberate boundary: throughline is a validator and an injector,
never a document editor (see the ``non_goal`` NG-0001).
"""
from __future__ import annotations

import re
from pathlib import Path

from .graph import Index
from .validate import FilterError, eval_filter

_KINDS = ("item", "table", "matrix", "count", "catalog", "unused")

# A marked region: the open marker, its generated body, and the end marker. The
# body is matched non-greedily so adjacent regions do not merge; DOTALL lets a
# body span lines; IGNORECASE tolerates `TL:` and `tl:`.
_BLOCK = re.compile(
    r"(?P<open><!--\s*tl:(?P<kind>item|table|matrix|count|catalog|unused)\s+(?P<arg>.*?)\s*-->)"
    r"(?P<body>.*?)"
    r"(?P<close><!--\s*tl:end\s*-->)",
    re.DOTALL | re.IGNORECASE,
)
# Any single tl: marker, to detect an unbalanced open/close the block regex skips.
_OPEN = re.compile(r"<!--\s*tl:(?:item|table|matrix|count|catalog|unused)\b", re.IGNORECASE)
_END = re.compile(r"<!--\s*tl:end\s*-->", re.IGNORECASE)

# An optional matrix selector (SR-0099): incoming:/outgoing:<link_type> before
# the filter. Reuses the coverage-rule grammar so the language is identical. An
# optional target-display suffix (SR-0110) follows the link type: @<primary> or
# @<primary>(<secondary>), each token being `uid` or an attribute name, so a cell
# can render a borrowed clause's own reference number instead of its UID.
_MATRIX_REL = re.compile(
    r"^(incoming|outgoing):(\w+)"
    r"(?:@(\w+)(?:\((\w+)\))?)?"
    r"\s*(.*)$",
    re.DOTALL,
)


def _parse_matrix_arg(
    arg: str,
) -> tuple[str | None, str | None, str | None, str | None, str]:
    """Split a tl:matrix argument into
    (direction, link_type, primary, secondary, filter). Absent a selector, the
    first four are None and the whole arg is the filter. `primary`/`secondary` are
    the target-display tokens (each `uid` or an attribute name); `primary` defaults
    to `uid` when a selector is present but no suffix is given."""
    m = _MATRIX_REL.match(arg.strip())
    if m:
        primary = m.group(3) or "uid"
        return m.group(1), m.group(2), primary, m.group(4), m.group(5).strip()
    return None, None, None, None, arg.strip()


class InjectError(ValueError):
    """A document marker could not be rendered (SR-0094): an unbalanced marker,
    an unknown item, or a malformed filter. Raised so drift is fixed, not hidden."""


class TargetResolver:
    """Resolves a matrix link target's display, liveness and attributes (SR-0110).

    The default reads only the local project, so injection behaves exactly as it
    did before this seam existed. A composing front end (tl-compose) supplies a
    resolver backed by the union graph, so a namespace-qualified target such as
    ``asvs:SR-0227`` — absent from the local project — still resolves its own
    ``source_ref`` and counts as live."""

    def __init__(self, project):
        self._project = project

    def present(self, uid: str) -> bool:
        """True if the target is a live item to be shown in a relationship cell."""
        return _is_live(self._project, uid)

    def display(self, uid: str) -> str:
        """The UID as it should appear in a cell. The local target string is
        already the form the author wrote (e.g. ``asvs:SR-0227``)."""
        return uid

    def attr(self, uid: str, name: str) -> str | None:
        """The value of attribute ``name`` on the target, or None if absent."""
        it = self._project.get(uid)
        if it is None:
            return None
        val = it.attrs.get(name)
        return None if val is None else str(val)


def has_markers(text: str) -> bool:
    """True if the text contains any tl: marker at all — the file is a throughline
    document. Files without markers are left completely untouched (SR-0095)."""
    return bool(_OPEN.search(text) or _END.search(text))


def _scan_refs(project, exclude_kinds: frozenset[str] = frozenset()) -> set[str]:
    """The set of item UIDs referenced by document markers, skipping any marker
    whose kind is in ``exclude_kinds``. A ``tl:item`` names its UID directly; a
    ``tl:table`` / ``tl:matrix`` / ``tl:count`` / ``tl:catalog`` publishes every
    item its filter selects. Malformed markers are ignored — reporting them is
    `tl docs`'s job, not a coverage question's."""
    root = Path(project.path)
    refs: set[str] = set()
    for pattern in project.schema.docs_paths:
        for p in sorted(root.glob(pattern)):
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except OSError:
                continue
            for m in _BLOCK.finditer(text):
                kind, arg = m.group("kind").lower(), m.group("arg").strip()
                if kind in exclude_kinds:
                    continue
                if kind == "item":
                    refs.add(arg)
                else:
                    expr = _parse_matrix_arg(arg)[4] if kind == "matrix" else arg
                    try:
                        refs.update(it.uid for it in _matching(project, expr))
                    except InjectError:
                        continue
    return refs


def referenced_uids(project) -> set[str] | None:
    """The set of item UIDs published by a configured document (SR-0096). Returns
    ``None`` when no ``[docs] paths`` are configured, so the ``unpublished`` rule
    is inert for projects that do not publish through throughline. A ``tl:unused``
    report does not itself publish the items it lists (SR-0112), so it is excluded;
    a ``tl:catalog`` does publish (its items appear in full) and so is counted."""
    if not project.schema.docs_paths:
        return None
    return _scan_refs(project, exclude_kinds=frozenset({"unused"}))


def inject_text(project, text: str, resolver: "TargetResolver | None" = None) -> str:
    """Return ``text`` with every marked region re-rendered from ``project``.
    Idempotent: injecting already-injected text yields identical output. Raises
    InjectError on an unbalanced marker so a broken document fails loudly.

    ``resolver`` (SR-0110) resolves matrix link targets' liveness and attributes;
    when omitted it reads the local ``project``, so behaviour is unchanged. A
    composing caller passes a union-backed resolver so borrowed clauses resolve."""
    if resolver is None:
        resolver = TargetResolver(project)
    opens = len(_OPEN.findall(text))
    ends = len(_END.findall(text))
    if opens != ends:
        raise InjectError(
            f"unbalanced tl: markers — {opens} opener(s) but {ends} tl:end(s); "
            "every opener needs exactly one tl:end")

    def _replace(m: re.Match) -> str:
        body = _render(project, m.group("kind").lower(), m.group("arg").strip(),
                       resolver)
        return f"{m.group('open')}\n{body}\n{m.group('close')}"

    return _BLOCK.sub(_replace, text)


# ------------------------------------------------------------------- renderers

def _render(project, kind: str, arg: str, resolver: "TargetResolver") -> str:
    if kind == "item":
        return _render_item(project, arg)
    if kind == "table":
        return _render_table(project, arg)
    if kind == "matrix":
        return _render_matrix(project, arg, resolver)
    if kind == "count":
        return _render_count(project, arg)
    if kind == "catalog":
        return _render_catalog(project, arg)
    if kind == "unused":
        return _render_unused(project, arg)
    raise InjectError(f"unknown directive 'tl:{kind}' (expected one of {_KINDS})")


def _render_item(project, uid: str) -> str:
    item = project.get(uid)
    if item is None:
        raise InjectError(
            f"tl:item references '{uid}' which is not in the graph — the document "
            "points at an item that no longer exists (drift)")
    if item.is_deleted:
        raise InjectError(
            f"tl:item references '{uid}' which is deleted — remove the marker or "
            "point it at a live item")
    head = f"**{item.uid} — {item.title or '(untitled)'}** — `{item.type}`, status `{item.status}`"
    lines = [head, ""]
    if item.text:
        lines += [f"> {ln}" if ln else ">" for ln in item.text.splitlines()]
        lines.append("")
    if item.rationale:
        lines.append(f"*Rationale:* {' '.join(item.rationale.split())}")
        lines.append("")
    if item.attrs:
        lines.append(" · ".join(f"**{k}**: {v}" for k, v in item.attrs.items()))
        lines.append("")
    return "\n".join(lines).rstrip()


def _matching(project, expr: str) -> list:
    """Live items matching an SR-0045 filter, in UID order. A malformed filter is
    fatal here (unlike coverage rules) so a broken document is fixed, not silently
    empty."""
    idx = Index.build(project)
    try:
        items = [it for it in project.items()
                 if not it.is_deleted and eval_filter(it, expr, idx)]
    except FilterError as e:
        raise InjectError(f"bad filter '{expr}': {e}") from e
    return sorted(items, key=lambda it: it.uid)


# Statuses that mean an item is no longer part of the live graph — mirrors the
# invalidate cascade's terminal set (grounding.py) so "live" means one thing.
_DEAD_STATUSES = ("rejected", "deleted")


def _is_live(project, uid: str) -> bool:
    """True if uid names an item present in the graph that is neither deleted nor
    rejected — i.e. something that genuinely realizes/relates, not a dead record."""
    it = project.get(uid)
    return it is not None and not it.is_deleted and it.status not in _DEAD_STATUSES


def _cell(value: str) -> str:
    """Escape a table cell so a pipe or newline in content cannot break the row."""
    return value.replace("|", "\\|").replace("\n", " ").strip()


def _render_table(project, expr: str) -> str:
    rows = _matching(project, expr)
    out = ["| UID | Type | Status | Title |", "|---|---|---|---|"]
    for it in rows:
        out.append(f"| {it.uid} | {it.type} | {it.status} | "
                   f"{_cell(it.title or '')} |")
    if not rows:
        out.append("| _(no matching items)_ |  |  |  |")
    return "\n".join(out)


def _render_count(project, expr: str) -> str:
    """The number of live items matching an SR-0045 filter, as a bare integer
    (SR-0109). Only live items count — a rejected or deleted item is not part of
    the graph a reader tallies, the same terminal-status set the matrix renderers
    use. A malformed filter fails injection; a filter matching nothing renders 0."""
    rows = [it for it in _matching(project, expr) if _is_live(project, it.uid)]
    return str(len(rows))


def _target_cell(resolver: "TargetResolver", uid: str,
                 primary: str, secondary: str | None) -> str:
    """Render one link target per the display spec (SR-0110). Each token is the
    literal ``uid`` (the resolver's display form) or an attribute name. An empty
    parenthesised secondary is dropped so a missing source_ref never renders as
    bare brackets."""
    def _tok(tok: str) -> str:
        return resolver.display(uid) if tok == "uid" else (resolver.attr(uid, tok) or "")
    head = _tok(primary)
    if secondary is None:
        return head
    tail = _tok(secondary)
    return f"{head} ({tail})" if tail else head


def _render_matrix(project, arg: str, resolver: "TargetResolver") -> str:
    """A traceability matrix. Default form: each matching item with what it grounds
    up to and what verifies it. With an incoming:/outgoing:<link_type> selector
    (SR-0099): each matching item and the items linked to it in that direction —
    e.g. incoming:implements over user_requirements lists each UR's realizers. An
    optional @<primary>(<secondary>) suffix (SR-0110) chooses how each target is
    rendered — its UID, an attribute such as source_ref, or UID plus attribute."""
    direction, ltype, primary, secondary, expr = _parse_matrix_arg(arg)
    idx = Index.build(project)
    rows = _matching(project, expr)

    if direction is not None:
        header = f"{ltype.capitalize()} ({direction})"
        out = [f"| UID | Title | {header} |", "|---|---|---|"]
        for it in rows:
            links = (idx.in_links(it.uid, {ltype}) if direction == "incoming"
                     else idx.out_links(it.uid, {ltype}))
            # Only live items count as realizers — a rejected or deleted item
            # does not realize anything (SR-0099). Liveness and attributes are
            # resolved through the resolver so borrowed targets resolve (SR-0110).
            cells = ", ".join(
                _target_cell(resolver, u, primary, secondary)
                for u, _k in links if resolver.present(u)) or "—"
            out.append(f"| {it.uid} | {_cell(it.title or '')} | {cells} |")
        if not rows:
            out.append("| _(no matching items)_ |  |  |")
        return "\n".join(out)

    ground = project.schema.ground_link_types
    out = ["| UID | Title | Traces to | Verified by |", "|---|---|---|---|"]
    for it in rows:
        up = ", ".join(t for t, _k in idx.out_links(it.uid, ground)) or "—"
        ver = ", ".join(s for s, _k in idx.in_links(it.uid, {"verifies"})) or "—"
        out.append(f"| {it.uid} | {_cell(it.title or '')} | {up} | {ver} |")
    if not rows:
        out.append("| _(no matching items)_ |  |  |  |")
    return "\n".join(out)


def _render_catalog(project, expr: str) -> str:
    """A full-item catalogue (SR-0111): every item matching an SR-0045 filter,
    rendered as the same full block a tl:item marker produces, in UID order. With
    a catch-all filter this is a self-maintaining master reference document."""
    rows = _matching(project, expr)
    if not rows:
        return "_(no matching items)_"
    return "\n\n".join(_render_item(project, it.uid) for it in rows)


def _render_unused(project, expr: str) -> str:
    """A report of items matching the filter that no *narrative* document cites
    (SR-0112) — items referenced only by a tl:catalog mirror, or by nothing, still
    count as unused. Requires configured [docs] paths to know what references
    exist; without them the report cannot be computed."""
    if not project.schema.docs_paths:
        return ("_(no [docs] paths configured — tl:unused cannot tell which items "
                "are referenced)_")
    # A catalogue mirror and the report's own listing are not narrative use.
    referenced = _scan_refs(project, exclude_kinds=frozenset({"catalog", "unused"}))
    rows = [it for it in _matching(project, expr) if it.uid not in referenced]
    out = ["| UID | Type | Status | Title |", "|---|---|---|---|"]
    for it in rows:
        out.append(f"| {it.uid} | {it.type} | {it.status} | "
                   f"{_cell(it.title or '')} |")
    if not rows:
        out.append("| _(every matching item is referenced)_ |  |  |  |")
    return "\n".join(out)
