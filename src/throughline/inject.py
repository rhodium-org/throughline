# Copyright (c) 2026 Time Back Solutions Limited
# SPDX-License-Identifier: Apache-2.0
"""In-place marker injection (SR-0094) — the drift-free document seam (UR-0024).

A requirements document is a human-owned Markdown file that interleaves the
author's own prose with item content drawn from the graph. The author marks the
places where generated content belongs with HTML comments (invisible on GitHub);
throughline regenerates *only* the marked regions, leaving every other byte of
the file untouched. Because the content is a reference that is re-rendered in
place — never a hand-maintained copy — it cannot silently drift from the graph.

Three directives, each opened by ``<!-- tl:<directive> <arg> -->`` and closed by
``<!-- tl:end -->``:

    tl:item   <UID>     one item rendered as a block
    tl:table  <filter>  a table of items matching an SR-0045 filter
    tl:matrix <filter>  a traceability matrix for matching items

Anything richer (HTML, PDF, a whole book) is delegated to external tools
(pandoc, mdBook) run over the injected files. Keeping the engine to these three
directives is a deliberate boundary: throughline is a validator and an injector,
never a document editor (see the ``non_goal`` NG-0001).
"""
from __future__ import annotations

import re
from pathlib import Path

from .graph import Index
from .validate import FilterError, eval_filter

_KINDS = ("item", "table", "matrix")

# A marked region: the open marker, its generated body, and the end marker. The
# body is matched non-greedily so adjacent regions do not merge; DOTALL lets a
# body span lines; IGNORECASE tolerates `TL:` and `tl:`.
_BLOCK = re.compile(
    r"(?P<open><!--\s*tl:(?P<kind>item|table|matrix)\s+(?P<arg>.*?)\s*-->)"
    r"(?P<body>.*?)"
    r"(?P<close><!--\s*tl:end\s*-->)",
    re.DOTALL | re.IGNORECASE,
)
# Any single tl: marker, to detect an unbalanced open/close the block regex skips.
_OPEN = re.compile(r"<!--\s*tl:(?:item|table|matrix)\b", re.IGNORECASE)
_END = re.compile(r"<!--\s*tl:end\s*-->", re.IGNORECASE)


class InjectError(ValueError):
    """A document marker could not be rendered (SR-0094): an unbalanced marker,
    an unknown item, or a malformed filter. Raised so drift is fixed, not hidden."""


def has_markers(text: str) -> bool:
    """True if the text contains any tl: marker at all — the file is a throughline
    document. Files without markers are left completely untouched (SR-0095)."""
    return bool(_OPEN.search(text) or _END.search(text))


def referenced_uids(project) -> set[str] | None:
    """The set of item UIDs referenced by any marker in a configured published
    document (SR-0096) — a ``tl:item`` names its UID directly; a ``tl:table`` /
    ``tl:matrix`` publishes every item its filter selects. Returns ``None`` when
    no ``[docs] paths`` are configured, so the ``unpublished`` rule is inert for
    projects that do not publish documents through throughline. Malformed markers
    are ignored here: reporting them is `tl docs`'s job, not the coverage rule's."""
    if not project.schema.docs_paths:
        return None
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
                if kind == "item":
                    refs.add(arg)
                else:
                    try:
                        refs.update(it.uid for it in _matching(project, arg))
                    except InjectError:
                        continue
    return refs


def inject_text(project, text: str) -> str:
    """Return ``text`` with every marked region re-rendered from ``project``.
    Idempotent: injecting already-injected text yields identical output. Raises
    InjectError on an unbalanced marker so a broken document fails loudly."""
    opens = len(_OPEN.findall(text))
    ends = len(_END.findall(text))
    if opens != ends:
        raise InjectError(
            f"unbalanced tl: markers — {opens} opener(s) but {ends} tl:end(s); "
            "every opener needs exactly one tl:end")

    def _replace(m: re.Match) -> str:
        body = _render(project, m.group("kind").lower(), m.group("arg").strip())
        return f"{m.group('open')}\n{body}\n{m.group('close')}"

    return _BLOCK.sub(_replace, text)


# ------------------------------------------------------------------- renderers

def _render(project, kind: str, arg: str) -> str:
    if kind == "item":
        return _render_item(project, arg)
    if kind == "table":
        return _render_table(project, arg)
    if kind == "matrix":
        return _render_matrix(project, arg)
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
    if item.attrs:
        lines.append(" · ".join(f"**{k}**: {v}" for k, v in item.attrs.items()))
        lines.append("")
    return "\n".join(lines).rstrip()


def _matching(project, expr: str) -> list:
    """Live items matching an SR-0045 filter, in UID order. A malformed filter is
    fatal here (unlike coverage rules) so a broken document is fixed, not silently
    empty."""
    try:
        items = [it for it in project.items()
                 if not it.is_deleted and eval_filter(it, expr)]
    except FilterError as e:
        raise InjectError(f"bad filter '{expr}': {e}") from e
    return sorted(items, key=lambda it: it.uid)


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


def _render_matrix(project, expr: str) -> str:
    """A traceability matrix: each matching item with what it grounds up to and
    what verifies it, so the 'why' and the coverage read at a glance."""
    idx = Index.build(project)
    ground = project.schema.ground_link_types
    rows = _matching(project, expr)
    out = ["| UID | Title | Traces to | Verified by |", "|---|---|---|---|"]
    for it in rows:
        up = ", ".join(t for t, _k in idx.out_links(it.uid, ground)) or "—"
        ver = ", ".join(s for s, _k in idx.in_links(it.uid, {"verifies"})) or "—"
        out.append(f"| {it.uid} | {_cell(it.title or '')} | {up} | {ver} |")
    if not rows:
        out.append("| _(no matching items)_ |  |  |  |")
    return "\n".join(out)
